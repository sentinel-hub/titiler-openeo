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
from typing import Any, Callable, Dict, List, Literal, Optional, Tuple, Union

import numpy
from rasterio.crs import CRS
from rio_tiler.models import ImageData
from rio_tiler.mosaic.methods import PixelSelectionMethod
from rio_tiler.types import BBox
from rio_tiler.utils import resize_array

from .data_model import RasterStack, get_first_item

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


def _get_pixel_selection_method_name(reducer: Callable) -> Optional[str]:
    """Get the PixelSelectionMethod name for a reducer function if applicable.

    Args:
        reducer: A reducer function

    Returns:
        The PixelSelectionMethod name if the reducer corresponds to one, None otherwise
    """
    reducer_name = getattr(reducer, "__name__", None)
    if reducer_name and reducer_name in PIXEL_SELECTION_REDUCERS:
        return PIXEL_SELECTION_REDUCERS[reducer_name]
    return None


pixel_methods = Literal[
    "first",
    "highest",
    "lowest",
    "mean",
    "median",
    "stdev",
    "lastbandlow",
    "lastbandhight",
    "count",
]


class DimensionNotAvailable(Exception):
    """Exception raised when a dimension is not available."""

    def __init__(self, dimension: str):
        self.dimension = dimension
        super().__init__(
            f"A dimension with the specified name '{dimension}' does not exist."
        )


def _initialize_pixel_selection_method(
    img: ImageData, pixsel_method, assets_used: List
):
    """Initialize pixel selection method with first image properties."""
    if len(assets_used) == 0:
        pixsel_method.cutline_mask = img.cutline_mask
        pixsel_method.width = img.width
        pixsel_method.height = img.height
        pixsel_method.count = img.count
        return img.crs, img.bounds, img.band_names
    return None, None, None


def _process_image_for_pixel_selection(
    img: ImageData,
    pixsel_method,
    key: str,
    assets_used: List,
    crs: Optional[CRS],
    bounds: Optional[BBox],
    band_names: Optional[List[str]],
) -> Tuple[Optional[CRS], Optional[BBox], Optional[List[str]]]:
    """Process a single image for pixel selection."""
    # Initialize on first image
    init_crs, init_bounds, init_band_names = _initialize_pixel_selection_method(
        img, pixsel_method, assets_used
    )
    if init_crs is not None:
        crs, bounds, band_names = init_crs, init_bounds, init_band_names

    # Validate band count
    assert (
        img.count == pixsel_method.count
    ), "Assets HAVE TO have the same number of bands"

    # Handle size differences
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

    assets_used.append(key)
    return crs, bounds, band_names


def _create_pixel_selection_result(
    pixsel_method,
    assets_used: List,
    crs: Optional[CRS],
    bounds: Optional[BBox],
    band_names: Optional[List[str]],
    pixel_selection: str,
) -> Dict[str, ImageData]:
    """Create the final pixel selection result."""
    return {
        "data": ImageData(
            pixsel_method.data,
            assets=assets_used,
            crs=crs,
            bounds=bounds,
            band_names=band_names if band_names is not None else [],
            metadata={"pixel_selection_method": pixel_selection},
        )
    }


def _process_timestamp_group_simple(
    timestamp_items,  # Can be Dict[str, ImageData] or dict-like object
    pixsel_method,
    assets_used: List,
    crs: Optional[CRS],
    bounds: Optional[BBox],
    band_names: Optional[List[str]],
    pixel_selection: str,
) -> Tuple[
    bool,
    Optional[Dict[str, ImageData]],
    Optional[CRS],
    Optional[BBox],
    Optional[List[str]],
]:
    """Process a timestamp group using already-loaded ImageData."""
    # Require dict-like interface with .items() method for consistency
    try:
        items = timestamp_items.items()
    except AttributeError as e:
        raise TypeError(
            "timestamp_items must implement dict-like interface with .items() method"
        ) from e

    # Process each image in the timestamp group
    for key, img in items:
        try:
            crs, bounds, band_names = _process_image_for_pixel_selection(
                img, pixsel_method, key, assets_used, crs, bounds, band_names
            )

            # Early termination check
            if pixsel_method.is_done and pixsel_method.data is not None:
                result = _create_pixel_selection_result(
                    pixsel_method, assets_used, crs, bounds, band_names, pixel_selection
                )
                return True, result, crs, bounds, band_names

        except Exception as e:
            # Skip failed tasks and continue
            logging.warning("Failed to load image %s: %s", key, str(e))
            continue

    return False, None, crs, bounds, band_names


def _process_sequential(
    data: RasterStack, pixsel_method, assets_used: List, pixel_selection: str
) -> Dict[str, ImageData]:
    """Process data sequentially for non-LazyRasterStack data."""
    crs: Optional[CRS] = None
    bounds: Optional[BBox] = None
    band_names: Optional[List[str]] = None

    for key in data.keys():
        try:
            img = data[key]
        except KeyError:
            continue

        crs, bounds, band_names = _process_image_for_pixel_selection(
            img, pixsel_method, key, assets_used, crs, bounds, band_names
        )

        if pixsel_method.is_done and pixsel_method.data is not None:
            return _create_pixel_selection_result(
                pixsel_method, assets_used, crs, bounds, band_names, pixel_selection
            )

    if pixsel_method.data is None:
        raise ValueError("Method returned an empty array")

    return _create_pixel_selection_result(
        pixsel_method, assets_used, crs, bounds, band_names, pixel_selection
    )


def apply_pixel_selection(
    data: RasterStack,
    pixel_selection: str = "first",
) -> RasterStack:
    """Apply PixelSelection method on a RasterStack with timestamp-based grouping.

    This function processes timestamp groups sequentially. For data sources that support
    timestamp-based grouping (such as LazyRasterStack), any concurrent execution is handled
    internally by the get_by_timestamp() method for each timestamp group.

    Returns:
        RasterStack: A single-image RasterStack containing the result of pixel selection
    """
    pixsel_method = PixelSelectionMethod[pixel_selection].value()
    assets_used: List = []
    crs: Optional[CRS] = None
    bounds: Optional[BBox] = None
    band_names: Optional[List[str]] = None

    # Check if data has timestamp-based grouping capability (LazyRasterStack)
    if hasattr(data, "groupby_timestamp") and hasattr(data, "timestamps"):
        timestamps = data.timestamps()

        if not timestamps:
            # Handle empty timestamps - maintain original error behavior
            if pixsel_method.data is None:
                raise ValueError("Method returned an empty array")
            return {
                "data": ImageData(numpy.array([]), assets=[], crs=None, bounds=None)
            }

        # Process timestamps in chronological order
        for timestamp in sorted(timestamps):
            timestamp_items = data.get_by_timestamp(timestamp)  # type: ignore[attr-defined]

            if not timestamp_items:
                continue

            # Process timestamp group using already-loaded ImageData
            terminated, result, crs, bounds, band_names = (
                _process_timestamp_group_simple(
                    timestamp_items,
                    pixsel_method,
                    assets_used,
                    crs,
                    bounds,
                    band_names,
                    pixel_selection,
                )
            )

            if terminated and result is not None:
                return result

            # Check for early termination after each timestamp group
            if pixsel_method.is_done and pixsel_method.data is not None:
                break

    else:
        # Fallback to sequential processing for non-LazyRasterStack data
        return _process_sequential(data, pixsel_method, assets_used, pixel_selection)

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
    if not isinstance(data, dict) or not data:
        raise ValueError(
            "Expected a non-empty RasterStack for temporal dimension reduction"
        )

    # Check if the reducer corresponds to a PixelSelectionMethod
    # This enables the efficient streaming approach for supported reducers
    pixel_selection_method = _get_pixel_selection_method_name(reducer)

    if pixel_selection_method is not None:
        # Use the efficient streaming approach via apply_pixel_selection
        # This processes data incrementally without loading everything into memory
        result = apply_pixel_selection(data, pixel_selection=pixel_selection_method)

        # Convert the result to match reduce_dimension's expected output format
        # apply_pixel_selection returns {"data": ImageData}, we return {"reduced": ImageData}
        result_img = result["data"]
        return {
            "reduced": ImageData(
                result_img.array,
                assets=result_img.assets,
                crs=result_img.crs,
                bounds=result_img.bounds,
                band_names=result_img.band_names,
                metadata={
                    "reduced_dimension": "temporal",
                    "reduction_method": getattr(reducer, "__name__", "custom_reducer"),
                },
            )
        }

    # Fallback: Apply the reducer to the stack for custom reducers
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

    # Get first successful image efficiently for LazyRasterStack - only for metadata
    first_img = get_first_item(data)

    return {
        "reduced": ImageData(
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
    }


def _reduce_spectral_dimension_single_image(
    data: ImageData,
    reducer: Callable,
) -> ImageData:
    """Reduce the spectral dimension of a single ImageData.

    Args:
        data: An ImageData with spectral dimension
        reducer: A reducer function to apply on the spectral dimension

    Returns:
        An ImageData with the spectral dimension reduced
    """
    # Pass only the array data (spectral bands) to the reducer, not the entire ImageData
    reduced_img_data = reducer(data=data.array)
    return ImageData(
        reduced_img_data,
        assets=data.assets,
        crs=data.crs,
        bounds=data.bounds,
        band_names=data.band_names if data.band_names is not None else [],
        metadata={
            "reduced_dimension": "spectral",
            "reduction_method": getattr(reducer, "__name__", "custom_reducer"),
        },
    )


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
    if not isinstance(data, dict) or not data:
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
    # The reducer output shape depends on whether it fully or partially reduced the spectral dimension
    if reduced_data.ndim == 3:
        # Output is (time, height, width) - fully reduced spectral dimension
        final_data = reduced_data
    elif reduced_data.ndim == 4:
        # Output is (reduced_bands, time, height, width) - partial spectral reduction
        # Move time axis back to position 0: (time, reduced_bands, height, width)
        final_data = numpy.moveaxis(reduced_data, 1, 0)
    elif reduced_data.ndim == 2:
        # Output is (time, height) or (height, width) - need to determine which
        # If first dimension matches number of images, it's (time, height)
        if reduced_data.shape[0] == len(images):
            # Add width dimension: (time, height, 1)
            final_data = reduced_data[:, :, numpy.newaxis]
        else:
            # Assume it's (height, width), expand for time: (1, height, width)
            final_data = reduced_data[numpy.newaxis, :, :]
            # Repeat for all time slices
            final_data = numpy.repeat(final_data, len(images), axis=0)
    else:
        # Unexpected dimensionality - try to infer
        logging.warning(
            f"Unexpected reducer output shape {reduced_data.shape} for spectral reduction. "
            f"Expected 3D (time, height, width) or 4D (time, bands, height, width). "
            f"Attempting to reshape..."
        )
        # If first dimension matches images, assume it's already correct
        if reduced_data.shape[0] == len(images):
            final_data = reduced_data
        else:
            # Try to move second dimension to first
            final_data = (
                numpy.moveaxis(reduced_data, 1, 0)
                if reduced_data.ndim > 1
                else reduced_data
            )

    # Split the result back into individual ImageData objects
    result = {}
    for idx, (key, meta) in enumerate(zip(keys, metadata_list)):
        # Extract the data for this time slice
        if final_data.ndim >= 1 and len(final_data) > idx:
            time_slice_data = final_data[idx]
        else:
            time_slice_data = final_data

        result[key] = ImageData(
            time_slice_data,
            assets=meta["assets"],
            crs=meta["crs"],
            bounds=meta["bounds"],
            band_names=meta["band_names"],
            metadata={
                "reduced_dimension": "spectral",
                "reduction_method": getattr(reducer, "__name__", "custom_reducer"),
            },
        )

    return result


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
        # Check if we have a single-image stack (common case from ImageData input)
        if len(data) == 1:
            # Get the single image and reduce its spectral dimension
            key = next(iter(data))
            return {key: _reduce_spectral_dimension_single_image(data[key], reducer)}
        else:
            return _reduce_spectral_dimension_stack(data, reducer)

    # Unsupported dimension
    else:
        raise DimensionNotAvailable(dimension)
