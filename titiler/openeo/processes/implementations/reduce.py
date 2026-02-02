"""titiler.openeo processed reduce.

CRITICAL DEVELOPER WARNING - REDUCER INVOCATION PATTERNS
=========================================================

When implementing dimension reduction functions in this module, it is CRITICAL to understand
that reducer functions may maintain internal state, caching, or other stateful behavior.
This means you MUST call each reducer function EXACTLY ONCE per reduction operation.

KEY REQUIREMENTS:
-----------------
1. **NEVER iterate and call the reducer multiple times** on individual items
   ❌ BAD: for item in data: result = reducer(data=item)
   ✅ GOOD: stacked = stack_all_data(data); result = reducer(data=stacked)

2. **Stack/combine all data FIRST, then call reducer ONCE**
   - For spectral reduction across time: Stack to (time, bands, h, w), transpose to
     (bands, time, h, w), call reducer ONCE
   - For temporal reduction: Stack to (time, bands, h, w), call reducer ONCE

3. **Understand the reduction axis**
   - Spectral reduction: Reduce across BANDS (axis depends on how data is stacked)
   - Temporal reduction: Reduce across TIME dimension
   - Always ensure the reducer operates on the correct axis

4. **Why this matters:**
   - Some reducers maintain caches of computed values
   - Calling a reducer multiple times can return incorrect/stale results
   - This bug has occurred MULTIPLE TIMES and caused production issues

5. **Testing requirements:**
   - Always test with a stateful/caching reducer
   - Verify reducer is called exactly once (use mock.call_count)
   - Test with multiple time slices and multiple bands

If you're modifying _reduce_spectral_dimension_stack or _reduce_temporal_dimension,
READ THIS ENTIRE WARNING CAREFULLY before making changes.
"""

import logging
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

import numpy
from rasterio.crs import CRS
from rio_tiler.models import ImageData
from rio_tiler.mosaic.methods import PixelSelectionMethod
from rio_tiler.types import BBox
from rio_tiler.utils import resize_array

from .data_model import ImageRef, RasterStack

__all__ = ["apply_pixel_selection", "reduce_dimension"]

# Mapping of reducer function names to their corresponding PixelSelectionMethod names
# This enables _reduce_temporal_dimension to use the efficient streaming approach
#
# NOTE: 'first' and 'last' from math.py are NOT included here because they have
# different semantics:
# - math.py first/last = first/last item in temporal order (chronologically)
# - PixelSelectionMethod first = first available (non-masked) pixel value
#
# Use 'firstpixel' reducer for pixel-wise first valid value behavior.
# Note: rio_tiler doesn't have a 'last' PixelSelectionMethod, so 'lastpixel'
# uses a fallback array-based implementation (not in this mapping).
PIXEL_SELECTION_REDUCERS = {
    "firstpixel": "first",  # first available pixel (fills masked values)
    "mean": "mean",
    "median": "median",
    "sd": "stdev",  # openEO uses 'sd' for standard deviation
    "stdev": "stdev",
    "count": "count",
    "highestpixel": "highest",
    "lowestpixel": "lowest",
    "lastbandlow": "lastbandlow",
    "lastbandhight": "lastbandhight",
}


class DimensionNotAvailable(Exception):
    """Exception raised when a dimension is not available."""

    def __init__(self, dimension: str):
        self.dimension = dimension
        super().__init__(
            f"A dimension with the specified name '{dimension}' does not exist."
        )


def _compute_aggregated_cutline_mask(
    cutline_masks: List[Optional[numpy.ndarray]],
) -> Optional[numpy.ndarray]:
    """Compute combined cutline mask from a list of individual masks.

    Combines masks using OR logic: a pixel is valid (False) if ANY image has it valid.
    If any image has no cutline_mask (None), all its pixels are considered valid,
    which means the aggregated result should indicate all pixels are valid.

    Args:
        cutline_masks: List of cutline_mask arrays (or None)

    Returns:
        Combined cutline mask, or None if all pixels are valid
    """
    if not cutline_masks:
        return None

    # If any image has no cutline_mask, all its pixels are valid
    # With OR logic, this means all pixels in the aggregate are valid
    if any(m is None for m in cutline_masks):
        return None

    # All masks are valid arrays at this point - filter to satisfy mypy
    valid_masks: List[numpy.ndarray] = [m for m in cutline_masks if m is not None]

    # Start with first mask
    aggregated = valid_masks[0].copy()

    # OR combination: pixel is valid (False) if ANY image has it valid
    # Since True = outside footprint, minimum gives the union of valid areas
    for mask in valid_masks[1:]:
        aggregated = numpy.minimum(aggregated, mask)

    return aggregated


def _create_pixel_selection_result(
    pixsel_method,
    assets_used: List,
    crs: Optional[CRS],
    bounds: Optional[BBox],
    band_names: Optional[List[str]],
    pixel_selection: str,
) -> RasterStack:
    """Create the final pixel selection result."""
    result_img = ImageData(
        pixsel_method.data,
        assets=assets_used,
        crs=crs,
        bounds=bounds,
        band_names=band_names if band_names is not None else [],
        metadata={"pixel_selection_method": pixel_selection},
    )
    # Use the first asset's datetime as the result key
    result_datetime = assets_used[0] if assets_used else datetime.now()
    return RasterStack.from_images({result_datetime: result_img})


def _collect_images_from_data(
    data: RasterStack,
) -> List[Tuple[datetime, ImageRef]]:
    """Collect all image references from a RasterStack.

    This function ALWAYS returns ImageRef instances. RasterStack should always
    have image refs available when created with proper dimensions.

    This enables deferred execution and cutline mask computation without loading
    actual pixel data, while still providing a uniform interface.

    Args:
        data: A RasterStack

    Returns:
        List of (datetime, ImageRef) tuples in temporal order
    """
    # Get ImageRef instances - RasterStack should always have them
    image_refs = data.get_image_refs()
    if image_refs:
        return image_refs

    # Fallback: create ImageRef from pre-loaded images
    # This handles RasterStacks created without dimension info
    result: List[Tuple[datetime, ImageRef]] = []
    for key in data.keys():
        try:
            result.append((key, ImageRef.from_image(image=data[key])))
        except KeyError:
            continue
    return result


def _feed_image_to_pixsel(
    img: ImageData,
    pixsel_method: Any,
) -> None:
    """Feed an image to the pixel selection method, handling size differences."""
    if any([img.width != pixsel_method.width, img.height != pixsel_method.height]):
        logging.warning(
            "Cannot concatenate images with different size. Will resize using first asset width/height"
        )
        h = pixsel_method.height
        w = pixsel_method.width
        pixsel_method.feed(
            numpy.ma.MaskedArray(
                resize_array(img.array.data, h, w),
                mask=resize_array(img.array.mask * 1, h, w).astype("bool"),
            )
        )
    else:
        pixsel_method.feed(img.array)


def apply_pixel_selection(
    data: RasterStack,
    pixel_selection: str = "first",
) -> RasterStack:
    """Apply PixelSelection method on a RasterStack with timestamp-based grouping.

    This function first collects all image refs and computes the aggregated cutline mask
    (without executing tasks for lazy refs), then processes images for pixel selection.
    This ensures early termination works correctly by knowing upfront which pixels will
    be covered by any image.

    All items are accessed through the ImageRef interface, which provides uniform access
    to metadata and data regardless of whether the image is lazy or pre-loaded.

    Returns:
        RasterStack: A single-image RasterStack containing the result of pixel selection
    """
    pixsel_method = PixelSelectionMethod[pixel_selection].value()
    assets_used: List = []
    crs: Optional[CRS] = None
    bounds: Optional[BBox] = None
    band_names: Optional[List[str]] = None

    # Collect all image refs (without executing tasks for lazy refs)
    all_items = _collect_images_from_data(data)

    if not all_items:
        raise ValueError("Method returned an empty array")

    # Compute aggregated cutline mask from all refs (using metadata, not pixel data)
    cutline_masks = [ref.cutline_mask() for _, ref in all_items]
    aggregated_cutline = _compute_aggregated_cutline_mask(cutline_masks)

    # Initialize pixsel_method from first ref's metadata
    first_key, first_ref = all_items[0]
    pixsel_method.width = first_ref.width
    pixsel_method.height = first_ref.height
    pixsel_method.count = first_ref.count
    crs = first_ref.crs
    bounds = first_ref.bounds
    band_names = first_ref.band_names

    # Set the aggregated cutline mask
    if aggregated_cutline is not None:
        pixsel_method.cutline_mask = aggregated_cutline

    # Process items for pixel selection
    for key, ref in all_items:
        # Realize the image (execute task for lazy refs, return cached for eager refs)
        img = ref.realize()

        # Validate band count
        assert (
            img.count == pixsel_method.count
        ), "Assets HAVE TO have the same number of bands"

        _feed_image_to_pixsel(img, pixsel_method)
        assets_used.append(key)

        # Early termination check
        if pixsel_method.is_done and pixsel_method.data is not None:
            break

    if pixsel_method.data is None:
        raise ValueError("Method returned an empty array")

    return _create_pixel_selection_result(
        pixsel_method, assets_used, crs, bounds, band_names, pixel_selection
    )


def _reduce_temporal_dimension(
    data: RasterStack,
    reducer: Callable,
) -> RasterStack:
    """Reduce the temporal dimension of a RasterStack.

    This function uses the efficient streaming approach via PixelSelectionMethod
    for supported reducers (first, mean, median, stdev, count, highest, lowest,
    lastbandlow, lastbandhight). For custom reducers, it falls back to loading
    all data and applying the reducer.

    Args:
        data: A RasterStack with temporal dimension
        reducer: A reducer function to apply on the temporal dimension

    Returns:
        A RasterStack with a single reduced temporal result

    Raises:
        ValueError: If the data is not a valid RasterStack or reducer doesn't return expected format
    """
    # Validate input - must be a non-empty RasterStack
    if not data:
        raise ValueError(
            "Expected a non-empty RasterStack for temporal dimension reduction"
        )

    # Note: The reducer will determine how much data it actually needs
    # Some reducers might be able to work with partial data
    reduced_array = reducer(data=data)

    # Validate the reducer output - must NOT be a RasterStack or dict
    if isinstance(reduced_array, dict):
        raise ValueError(
            "The reducer must return an array-like object for temporal dimension reduction, "
            "not a RasterStack (dict). The reducer should collapse the temporal dimension "
            "and return the resulting array directly."
        )

    # Check if it's array-like (more compliant than checking only numpy.ndarray)
    try:
        reduced_array = numpy.asarray(reduced_array)
    except (TypeError, ValueError) as e:
        reducer_type = type(reduced_array).__name__
        raise ValueError(
            f"The reducer must return an array-like object for temporal dimension reduction, "
            f"but returned {reducer_type} which cannot be converted to an array. "
            f"Expected array-like data with dimensions like (bands, height, width) or (height, width)."
        ) from e

    # Get first successful image efficiently - only for metadata
    first_img = data.first if hasattr(data, "first") else next(iter(data.values()))

    reduced_img = ImageData(
        reduced_array,  # Use the reduced array directly since it's already collapsed
        assets=first_img.assets,
        crs=first_img.crs,
        bounds=first_img.bounds,
        band_names=first_img.band_names,
        metadata={
            "reduced_dimension": "temporal",
            "reduction_method": getattr(reducer, "__name__", "custom_reducer"),
        },
    )
    # Use the first timestamp from the data as the result key
    first_key = next(iter(data.keys()))
    return RasterStack.from_images({first_key: reduced_img})


def _reshape_reduced_spectral_data(
    reduced_data: numpy.ndarray, num_images: int
) -> numpy.ndarray:
    """Transform reducer output back to (time, ...) format for splitting.

    Args:
        reduced_data: Output from the spectral reducer
        num_images: Number of time slices expected

    Returns:
        Array reshaped to (time, ...) format
    """
    if reduced_data.ndim == 3:
        # Output is (time, height, width) - fully reduced spectral dimension
        return reduced_data
    elif reduced_data.ndim == 4:
        # Output is (reduced_bands, time, height, width) - partial spectral reduction
        # Move time axis back to position 0: (time, reduced_bands, height, width)
        return numpy.moveaxis(reduced_data, 1, 0)
    elif reduced_data.ndim == 2:
        # Output is (time, height) or (height, width) - need to determine which
        # If first dimension matches number of images, it's (time, height)
        if reduced_data.shape[0] == num_images:
            # Add width dimension: (time, height, 1)
            return reduced_data[:, :, numpy.newaxis]
        else:
            # Assume it's (height, width), expand for time: (1, height, width)
            expanded = reduced_data[numpy.newaxis, :, :]
            # Repeat for all time slices
            return numpy.repeat(expanded, num_images, axis=0)
    else:
        # Unexpected dimensionality - try to infer
        logging.warning(
            f"Unexpected reducer output shape {reduced_data.shape} for spectral reduction. "
            f"Expected 3D (time, height, width) or 4D (time, bands, height, width). "
            f"Attempting to reshape..."
        )
        # If first dimension matches images, assume it's already correct
        if reduced_data.shape[0] == num_images:
            return reduced_data
        else:
            # Try to move second dimension to first
            return (
                numpy.moveaxis(reduced_data, 1, 0)
                if reduced_data.ndim > 1
                else reduced_data
            )


def _determine_output_band_count(final_data: numpy.ndarray) -> int:
    """Determine the number of output bands from the final reduced data.

    Args:
        final_data: The reshaped data in (time, ...) format

    Returns:
        Number of output bands (0 if cannot be determined)
    """
    if final_data.ndim >= 1 and len(final_data) > 0:
        sample_slice = final_data[0] if final_data.ndim > 2 else final_data
        if sample_slice.ndim == 2:
            # (height, width) - single band output
            return 1
        elif sample_slice.ndim == 3:
            # (bands, height, width) - multi-band output
            return sample_slice.shape[0]
    return 0


def _reduce_spectral_dimension_stack(
    data: RasterStack,
    reducer: Callable,
) -> RasterStack:
    """Reduce the spectral dimension of a RasterStack.

    CRITICAL: This function calls the reducer EXACTLY ONCE to handle reducers with
    internal state/caching. All images are stacked first, then the reducer is invoked
    once on the combined data. DO NOT MODIFY to call reducer per-image.

    Args:
        data: A RasterStack with spectral dimension
        reducer: A reducer function to apply on the spectral dimension.
                 The reducer will be called ONCE with shape (bands, time, height, width)
                 and must reduce along axis 0 (bands).

    Returns:
        A RasterStack with the spectral dimension reduced for each image

    Raises:
        ValueError: If the reducer doesn't return valid data
    """
    # Validate input - must be a non-empty RasterStack
    if not data:
        raise ValueError(
            "Expected a non-empty RasterStack for spectral dimension reduction"
        )

    # Load all images first and collect metadata
    images = []
    keys = []
    metadata_list = []

    for key in data.keys():
        try:
            img = data[key]  # Access each image individually
            images.append(img.array)
            keys.append(key)
            metadata_list.append(
                {
                    "crs": img.crs,
                    "bounds": img.bounds,
                    "band_names": img.band_names if img.band_names is not None else [],
                    "assets": [key],
                }
            )
        except KeyError as e:
            # Log task failures but continue processing other keys
            logging.warning(
                "Failed to load data for key '%s' during spectral dimension reduction: %s. "
                "This may be due to task execution failure in lazy loading. Skipping this item.",
                key,
                str(e),
            )
            continue

    if not images:
        raise ValueError("No valid images loaded for spectral dimension reduction")

    # CRITICAL: Stack all images FIRST, then call reducer ONCE
    # This is required for reducers with internal caching/state
    # Stack all images along a new temporal dimension (axis 0)
    # Shape will be (time, bands, height, width)
    stacked_data = numpy.stack(images, axis=0)

    # To reduce the spectral dimension while maintaining time separation,
    # we move the bands axis to the front: (bands, time, height, width)
    # This allows the reducer to operate on the bands dimension (axis 0)
    transposed_data = numpy.moveaxis(stacked_data, 1, 0)

    # CRITICAL: Call the reducer EXACTLY ONCE with ALL the data
    # The reducer will reduce along axis 0 (bands dimension)
    # Input shape: (bands, time, height, width)
    # Expected output shape: (time, height, width) for full reduction
    #                    or: (reduced_bands, time, height, width) for partial reduction
    reduced_data = reducer(data=transposed_data)

    # Validate the reducer output - must NOT be a RasterStack or dict
    if isinstance(reduced_data, dict):
        raise ValueError(
            "The reducer must return an array-like object for spectral dimension reduction, "
            "not a RasterStack (dict). The reducer should process the spectral bands "
            "and return the resulting array directly."
        )

    # Check if it's array-like
    try:
        reduced_data = numpy.asarray(reduced_data)
    except (TypeError, ValueError) as e:
        reducer_type = type(reduced_data).__name__
        raise ValueError(
            f"The reducer must return an array-like object for spectral dimension reduction, "
            f"but returned {reducer_type} which cannot be converted to an array. "
            f"Expected array-like data with reduced spectral bands."
        ) from e

    # Transform the result back to (time, ...) format for splitting into time slices
    final_data = _reshape_reduced_spectral_data(reduced_data, len(images))

    # Determine output band count for band_names handling
    num_output_bands = _determine_output_band_count(final_data)

    # Split the result back into individual ImageData objects
    result = {}
    for idx, (key, meta) in enumerate(zip(keys, metadata_list)):
        # Extract the data for this time slice
        if final_data.ndim >= 1 and len(final_data) > idx:
            time_slice_data = final_data[idx]
        else:
            time_slice_data = final_data

        # Adjust band_names to match output band count
        # If band count changed, clear band_names to avoid mismatches
        output_band_names = []
        if (
            num_output_bands > 0
            and meta["band_names"]
            and len(meta["band_names"]) == num_output_bands
        ):
            output_band_names = meta["band_names"]

        result[key] = ImageData(
            time_slice_data,
            assets=meta["assets"],
            crs=meta["crs"],
            bounds=meta["bounds"],
            band_names=output_band_names,
            metadata={
                "reduced_dimension": "spectral",
                "reduction_method": getattr(reducer, "__name__", "custom_reducer"),
            },
        )

    return RasterStack.from_images(result)


def reduce_dimension(
    data: RasterStack,
    reducer: Callable,
    dimension: str,
    context: Optional[Dict[str, Any]] = None,
) -> Union[RasterStack, ImageData]:
    """Applies a reducer to a data cube dimension by collapsing all the values along the specified dimension.

    Args:
        data: A RasterStack data cube
        reducer: A reducer function to apply on the specified dimension
        dimension: The name of the dimension over which to reduce
        context: Additional data to be passed to the reducer

    Returns:
        A data cube with the newly computed values, missing the given dimension

    Raises:
        DimensionNotAvailable: If the specified dimension does not exist
        ValueError: If the input data is invalid or the reducer returns invalid data
    """
    # Normalize dimension name
    dim_lower = dimension.lower()

    # Handle temporal dimension
    if dim_lower in ["t", "temporal", "time"]:
        # If there's only one item in the stack, there's no temporal dimension to reduce
        if len(data) <= 1:
            return data

        return _reduce_temporal_dimension(data, reducer)

    # Handle spectral dimension
    elif dim_lower in ["bands", "spectral"]:
        # Use unified stack reduction for both single and multi-image cases
        return _reduce_spectral_dimension_stack(data, reducer)

    # Unsupported dimension
    else:
        raise DimensionNotAvailable(dimension)
