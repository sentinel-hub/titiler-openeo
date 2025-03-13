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
        super().__init__(f"A dimension with the specified name '{dimension}' does not exist.")


def apply_pixel_selection(
    data: RasterStack,
    pixel_selection: str = "first",
) -> ImageData:
    """Apply PixelSelection method on a RasterStack."""
    pixsel_method = PixelSelectionMethod[pixel_selection].value()

    assets_used: List = []
    crs: Optional[CRS]
    bounds: Optional[BBox]
    band_names: List[str]

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
                band_names=band_names,
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
        band_names=band_names,
        metadata={
            "pixel_selection_method": pixel_selection,
        },
    )


def reduce_dimension(
    data: Union[RasterStack, ImageData],
    reducer: Callable,
    dimension: str,
    context: Optional[Dict[str, Any]] = None,
) -> ImageData:
    """Applies a reducer to a data cube dimension by collapsing all the values along the specified dimension.
    
    Args:
        data: A data cube (RasterStack or ImageData)
        reducer: A reducer function to apply on the specified dimension
        dimension: The name of the dimension over which to reduce
        context: Additional data to be passed to the reducer
        
    Returns:
        A data cube with the newly computed values, missing the given dimension
        
    Raises:
        DimensionNotAvailable: If the specified dimension does not exist
    """
    # Currently, we only support the temporal dimension for RasterStack
    if dimension.lower() in ["t", "temporal", "time"]:
        if isinstance(data, ImageData):
            # If it's already a single ImageData, there's no temporal dimension to reduce
            raise DimensionNotAvailable(dimension)
        
        if not isinstance(data, dict) or not data:
            raise ValueError("Expected a non-empty RasterStack for temporal dimension reduction")
        
        # Extract arrays from all images in the stack
        # Create a labeled array for the reducer
        labeled_arrays = []
        for key, img in data.items():
            labeled_arrays.append({"label": key, "data": img.array})
        
        # Apply the reducer to the labeled arrays
        context = context or {}
        reduced_array = reducer(labeled_arrays, context)
        
        # Get metadata from the first image in the stack
        first_key = next(iter(data))
        first_img = data[first_key]
        
        # Create a new ImageData with the reduced array
        return ImageData(
            reduced_array,
            assets=[key for key in data.keys()],
            crs=first_img.crs,
            bounds=first_img.bounds,
            band_names=first_img.band_names,
            metadata={
                "reduced_dimension": dimension,
                "reduction_method": getattr(reducer, "__name__", "custom_reducer"),
            },
        )
    else:
        # For other dimensions, we need to check if they exist
        if isinstance(data, ImageData):
            # For ImageData, we can reduce along the band dimension
            if dimension.lower() in ["bands", "spectral"]:
                # This would require a different implementation that reduces across bands
                # For now, we'll raise an error
                raise NotImplementedError(f"Reduction along '{dimension}' dimension is not implemented yet")
            else:
                raise DimensionNotAvailable(dimension)
        else:
            raise DimensionNotAvailable(dimension)
