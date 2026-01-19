"""titiler.openeo processed reduce."""

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

    # Apply the reducer to the stack
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

    Args:
        data: A RasterStack with spectral dimension
        reducer: A reducer function to apply on the spectral dimension

    Returns:
        A RasterStack with the spectral dimension reduced for each image

    Raises:
        ValueError: If the reducer doesn't return valid data
    """
    if not isinstance(data, dict) or not data:
        raise ValueError(
            "Expected a non-empty RasterStack for spectral dimension reduction"
        )

    # Create a new stack with the reduced data
    result = {}

    # Iterate through keys instead of items() to avoid executing all tasks at once
    # Apply the reducer to each individual time slice
    for key in data.keys():
        try:
            img = data[key]  # Access each image individually

            # Apply the reducer to this individual image's spectral bands (array only)
            reduced_img_data = reducer(data=img.array)

            # Validate the reducer output - must NOT be a RasterStack or dict
            if isinstance(reduced_img_data, dict):
                raise ValueError(
                    f"The reducer must return an array-like object for spectral dimension reduction "
                    f"of image {key}, not a RasterStack (dict). The reducer should process the spectral bands "
                    f"and return the resulting array directly."
                )

            # Check if it's array-like (more compliant than checking only numpy.ndarray)
            try:
                reduced_img_data = numpy.asarray(reduced_img_data)
            except (TypeError, ValueError) as e:
                reducer_type = type(reduced_img_data).__name__
                raise ValueError(
                    f"The reducer must return an array-like object for spectral dimension reduction "
                    f"of image {key}, but returned {reducer_type} which cannot be converted to an array. "
                    f"Expected array-like data with reduced spectral bands."
                ) from e

            result[key] = ImageData(
                reduced_img_data,
                assets=[key],
                crs=img.crs,
                bounds=img.bounds,
                band_names=img.band_names if img.band_names is not None else [],
                metadata={
                    "reduced_dimension": "spectral",
                    "reduction_method": getattr(reducer, "__name__", "custom_reducer"),
                },
            )
        except KeyError as e:
            # Log task failures but continue processing other keys
            # This maintains backward compatibility with task-based execution
            logging.warning(
                "Failed to load data for key '%s' during spectral dimension reduction: %s. "
                "This may be due to task execution failure in lazy loading. Skipping this item.",
                key,
                str(e),
            )
            continue

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
