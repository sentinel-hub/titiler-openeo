"""titiler.openeo.processes.implementations image methods."""

from typing import Dict, Sequence

import numpy
from rio_tiler.colormap import cmap as default_cmap
from rio_tiler.types import ColorMapType

from .data_model import ImageData, LazyRasterStack, RasterStack

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


def image_indexes(data: RasterStack, indexes: Sequence[int]) -> RasterStack:
    """Select indexes from a RasterStack.

    Args:
        data: RasterStack to process
        indexes: Sequence of band indexes to select (1-based)

    Returns:
        RasterStack with selected indexes
    """
    # Apply to each item in the RasterStack
    result: Dict[str, ImageData] = {}
    for key, img_data in data.items():
        result[key] = _apply_image_indexes(img_data, indexes)
    return LazyRasterStack.from_images(result)


def to_array(
    data: RasterStack,
) -> Dict[str, numpy.ma.MaskedArray]:
    """Convert RasterStack to array(s).

    Args:
        data: RasterStack to convert

    Returns:
        Dictionary mapping keys to numpy.ma.MaskedArray
    """
    # Convert each item to array
    return {key: img_data.array for key, img_data in data.items()}


def _apply_color_formula(data: ImageData, formula: str) -> ImageData:
    """Apply color formula to a single ImageData."""
    return data.apply_color_formula(formula)


def color_formula(data: RasterStack, formula: str) -> RasterStack:
    """Apply color formula to RasterStack.

    Args:
        data: RasterStack to process
        formula: Color formula to apply

    Returns:
        RasterStack with color formula applied
    """
    # Apply to each item in the RasterStack
    result: Dict[str, ImageData] = {}
    for key, img_data in data.items():
        result[key] = _apply_color_formula(img_data, formula)
    return LazyRasterStack.from_images(result)


def get_colormap(name: str) -> ColorMapType:
    """Return rio-tiler colormap."""
    return default_cmap.get(name)


def _apply_colormap(data: ImageData, colormap: ColorMapType) -> ImageData:
    """Apply colormap to a single ImageData."""
    return data.apply_colormap(colormap)


def colormap(data: RasterStack, colormap: ColorMapType) -> RasterStack:
    """Apply colormap to RasterStack.

    Args:
        data: RasterStack to process
        colormap: Colormap to apply

    Returns:
        RasterStack with colormap applied
    """
    # Apply to each item in the RasterStack
    result: Dict[str, ImageData] = {}
    for key, img_data in data.items():
        result[key] = _apply_colormap(img_data, colormap)
    return LazyRasterStack.from_images(result)
