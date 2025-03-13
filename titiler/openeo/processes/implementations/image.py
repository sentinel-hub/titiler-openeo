"""titiler.openeo.processes.implementations image methods."""

from typing import Dict, Sequence, Union

import numpy
from rio_tiler.colormap import cmap as default_cmap
from rio_tiler.types import ColorMapType

from .data_model import ImageData, RasterStack

__all__ = [
    "image_indexes",
    "to_array",
    "color_formula",
    "colormap",
    "get_colormap",
]


def _apply_image_indexes(data: ImageData, indexes: Sequence[int]) -> ImageData:
    """Select indexes from a single ImageData."""
    if not all(v > 0 for v in indexes):
        raise IndexError(f"Indexes value must be >= 1, {indexes}")

    if not all(v <= data.count + 1 for v in indexes):
        raise IndexError(f"Indexes value must be =< {data.count + 1}, {indexes}")

    stats = None
    if stats := data.dataset_statistics:
        stats = [stats[ix - 1] for ix in indexes]

    return ImageData(
        data.array[[idx - 1 for idx in indexes]],
        assets=data.assets,
        crs=data.crs,
        bounds=data.bounds,
        band_names=[data.band_names[ix - 1] for ix in indexes],
        metadata=data.metadata,
        dataset_statistics=stats,
        cutline_mask=data.cutline_mask,
    )


def image_indexes(data: Union[ImageData, RasterStack], indexes: Sequence[int]) -> Union[ImageData, RasterStack]:
    """Select indexes from an ImageData or RasterStack.

    Args:
        data: ImageData or RasterStack to process
        indexes: Sequence of band indexes to select (1-based)

    Returns:
        ImageData or RasterStack with selected indexes
    """
    # If data is a single ImageData, apply directly
    if isinstance(data, ImageData):
        return _apply_image_indexes(data, indexes)

    # If data is a RasterStack (dictionary), apply to each item
    if isinstance(data, dict):
        result: Dict[str, ImageData] = {}
        for key, img_data in data.items():
            result[key] = _apply_image_indexes(img_data, indexes)
        return result

    # If we get here, data is neither ImageData nor a dictionary
    raise TypeError(f"Expected ImageData or RasterStack, got {type(data)}")


def to_array(data: Union[ImageData, RasterStack]) -> Union[numpy.ma.MaskedArray, Dict[str, numpy.ma.MaskedArray]]:
    """Convert ImageData or RasterStack to array(s).

    Args:
        data: ImageData or RasterStack to convert
        
    Returns:
        For ImageData: numpy.ma.MaskedArray
        For RasterStack: Dictionary mapping keys to numpy.ma.MaskedArray
    """
    if isinstance(data, ImageData):
        return data.array
    
    if isinstance(data, dict):
        return {key: img_data.array for key, img_data in data.items()}
    
    raise TypeError(f"Expected ImageData or RasterStack, got {type(data)}")


def _apply_color_formula(data: ImageData, formula: str) -> ImageData:
    """Apply color formula to a single ImageData."""
    return data.apply_color_formula(formula)


def color_formula(data: Union[ImageData, RasterStack], formula: str) -> Union[ImageData, RasterStack]:
    """Apply color formula to ImageData or RasterStack.
    
    Args:
        data: ImageData or RasterStack to process
        formula: Color formula to apply
        
    Returns:
        ImageData or RasterStack with color formula applied
    """
    if isinstance(data, ImageData):
        return _apply_color_formula(data, formula)
    
    if isinstance(data, dict):
        result: Dict[str, ImageData] = {}
        for key, img_data in data.items():
            result[key] = _apply_color_formula(img_data, formula)
        return result
    
    raise TypeError(f"Expected ImageData or RasterStack, got {type(data)}")


def get_colormap(name: str) -> ColorMapType:
    """Return rio-tiler colormap."""
    return default_cmap.get(name)


def _apply_colormap(data: ImageData, colormap: ColorMapType) -> ImageData:
    """Apply colormap to a single ImageData."""
    return data.apply_colormap(colormap)


def colormap(data: Union[ImageData, RasterStack], colormap: ColorMapType) -> Union[ImageData, RasterStack]:
    """Apply colormap to ImageData or RasterStack.
    
    Args:
        data: ImageData or RasterStack to process
        colormap: Colormap to apply
        
    Returns:
        ImageData or RasterStack with colormap applied
    """
    if isinstance(data, ImageData):
        return _apply_colormap(data, colormap)
    
    if isinstance(data, dict):
        result: Dict[str, ImageData] = {}
        for key, img_data in data.items():
            result[key] = _apply_colormap(img_data, colormap)
        return result
    
    raise TypeError(f"Expected ImageData or RasterStack, got {type(data)}")
