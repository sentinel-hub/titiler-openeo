"""titiler.openeo processed reduce."""

import warnings
from typing import Any, Callable, Dict, List, Literal, Optional, Union

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


def apply_pixel_selection(
    data: RasterStack,
    pixel_selection: str = "first",
) -> RasterStack:
    """Apply PixelSelection method on a RasterStack.

    Returns:
        RasterStack: A single-image RasterStack containing the result of pixel selection
    """
    # Use the original implementation for all selection methods
    # The optimization should be in early termination, not in skipping processing
    pixsel_method = PixelSelectionMethod[pixel_selection].value()

    assets_used: List = []
    crs: Optional[CRS] = None
    bounds: Optional[BBox] = None
    band_names: Optional[List[str]] = None

    # Iterate through keys instead of items() to avoid executing all tasks at once
    for key in data.keys():
        # Access each image individually - this triggers lazy loading for this specific key
        try:
            img = data[key]
        except KeyError:
            # Skip failed tasks and continue with the next one
            continue

        # On the first Image we set the properties
        if len(assets_used) == 0:
            crs = img.crs
            bounds = img.bounds
            band_names = img.band_names
            pixsel_method.cutline_mask = img.cutline_mask
            pixsel_method.width = img.width
            pixsel_method.height = img.height
            pixsel_method.count = img.count

        assert (
            img.count == pixsel_method.count
        ), "Assets HAVE TO have the same number of bands"

        if any(
            [
                img.width != pixsel_method.width,
                img.height != pixsel_method.height,
            ]
        ):
            warnings.warn(
                "Cannot concatenate images with different size. Will resize using fist asset width/heigh",
                UserWarning,
                stacklevel=2,
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

        # Store the key (which could be item ID) for tracking
        assets_used.append(key)

        # Early termination: if the pixel selection method is done, we can stop
        # This is the real optimization - stopping when we have enough data
        if pixsel_method.is_done and pixsel_method.data is not None:
            return {
                "data": ImageData(
                    pixsel_method.data,
                    assets=assets_used,
                    crs=crs,
                    bounds=bounds,
                    band_names=band_names if band_names is not None else [],
                    metadata={
                        "pixel_selection_method": pixel_selection,
                    },
                )
            }

    if pixsel_method.data is None:
        raise ValueError("Method returned an empty array")

    return {
        "data": ImageData(
            pixsel_method.data,
            assets=assets_used,
            crs=crs,
            bounds=bounds,
            band_names=band_names if band_names is not None else [],
            metadata={
                "pixel_selection_method": pixel_selection,
            },
        )
    }


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
    reduced_img_data = reducer(data=data)
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

    # Apply the reducer to the entire stack
    reduced_img_data = reducer(data=data)

    # Validate the reducer output - must NOT be a RasterStack or dict
    if isinstance(reduced_img_data, dict):
        raise ValueError(
            "The reducer must return an array-like object for spectral dimension reduction, "
            "not a RasterStack (dict). The reducer should process the spectral bands "
            "and return the resulting array directly."
        )

    # Check if it's array-like (more compliant than checking only numpy.ndarray)
    try:
        reduced_img_data = numpy.asarray(reduced_img_data)
    except (TypeError, ValueError) as e:
        reducer_type = type(reduced_img_data).__name__
        raise ValueError(
            f"The reducer must return an array-like object for spectral dimension reduction, "
            f"but returned {reducer_type} which cannot be converted to an array. "
            f"Expected array-like data with the same temporal dimension as input but reduced spectral bands."
        ) from e

    if reduced_img_data.shape[0] != len(data):
        raise ValueError(
            "The reduced data must have the same first dimension as the input stack"
        )

    # Create a new stack with the reduced data
    result = {}
    # Iterate through keys instead of items() to avoid executing all tasks at once
    for i, key in enumerate(data.keys()):
        try:
            img = data[key]  # Access each image individually
            result[key] = ImageData(
                reduced_img_data[i],
                assets=[key],
                crs=img.crs,
                bounds=img.bounds,
                band_names=img.band_names if img.band_names is not None else [],
                metadata={
                    "reduced_dimension": "spectral",
                    "reduction_method": getattr(reducer, "__name__", "custom_reducer"),
                },
            )
        except KeyError:
            # Skip failed tasks
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
