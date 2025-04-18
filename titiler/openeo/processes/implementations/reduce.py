"""titiler.openeo processed reduce."""

import warnings
from typing import Any, Callable, Dict, List, Literal, Optional, Union

import numpy
from rasterio.crs import CRS
from rio_tiler.models import ImageData
from rio_tiler.mosaic.methods import PixelSelectionMethod
from rio_tiler.types import BBox
from rio_tiler.utils import resize_array

from .data_model import RasterStack

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
) -> ImageData:
    """Apply PixelSelection method on a RasterStack."""
    pixsel_method = PixelSelectionMethod[pixel_selection].value()

    assets_used: List = []
    crs: Optional[CRS] = None
    bounds: Optional[BBox] = None
    band_names: Optional[List[str]] = None

    for datetime, img in data.items():
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

        assets_used.append(datetime)

        if pixsel_method.is_done and pixsel_method.data is not None:
            return ImageData(
                pixsel_method.data,
                assets=assets_used,
                crs=crs,
                bounds=bounds,
                band_names=band_names if band_names is not None else [],
                metadata={
                    "pixel_selection_method": pixel_selection,
                },
            )

    if pixsel_method.data is None:
        raise ValueError("Method returned an empty array")

    return ImageData(
        pixsel_method.data,
        assets=assets_used,
        crs=crs,
        bounds=bounds,
        band_names=band_names if band_names is not None else [],
        metadata={
            "pixel_selection_method": pixel_selection,
        },
    )


def _reduce_temporal_dimension(
    data: RasterStack,
    reducer: Callable,
) -> RasterStack:
    """Reduce the temporal dimension of a RasterStack.

    Args:
        data: A RasterStack with temporal dimension
        reducer: A reducer function to apply on the temporal dimension
        dimension: The name of the temporal dimension
        context: Additional data to be passed to the reducer

    Returns:
        A RasterStack with the temporal dimension reduced

    Raises:
        ValueError: If the data is not a valid RasterStack
    """
    if not isinstance(data, dict) or not data:
        raise ValueError(
            "Expected a non-empty RasterStack for temporal dimension reduction"
        )

    # Extract arrays from all images in the stack and create labeled arrays for the reducer
    # labeled_arrays = [{"label": key, "data": img.array} for key, img in data.items()]

    # Apply the reducer to the labeled arrays
    reduced_array = reducer(data=data)

    # Create a new stack with the reduced data
    if not isinstance(reduced_array, numpy.ndarray):
        raise ValueError(
            "The reducer must return a numpy array for temporal dimension reduction"
        )
    if reduced_array.shape[0] != 1:
        raise ValueError(
            "The reduced data must have the same first dimension as the input stack"
        )

    first_img = next(iter(data.values()))

    return {
        "reduced": ImageData(
            reduced_array[0],
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

    # Validate the reducer output
    if not isinstance(reduced_img_data, numpy.ndarray):
        raise ValueError(
            "The reducer must return a numpy array for spectral dimension reduction"
        )

    if reduced_img_data.shape[0] != len(data):
        raise ValueError(
            "The reduced data must have the same first dimension as the input stack"
        )

    # Create a new stack with the reduced data
    result = {}
    for i, (key, img) in enumerate(data.items()):
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
