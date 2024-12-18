"""titiler.openeo.processes.implementations image methods."""

from typing import Sequence

import numpy
from rio_tiler.colormap import cmap as default_cmap
from rio_tiler.types import ColorMapType

from .data_model import ImageData

__all__ = [
    "image_indexes",
    "to_array",
    "color_formula",
    "colormap",
    "get_colormap",
]


def image_indexes(data: ImageData, indexes: Sequence[int]) -> ImageData:
    """Select indexes from an ImageData."""
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


def to_array(data: ImageData) -> numpy.ma.MaskedArray:
    """Convert ImageData to array."""
    return data.array


def color_formula(data: ImageData, formula: str) -> ImageData:
    """Apply color formula to ImageData."""
    return data.apply_color_formula(formula)


def get_colormap(name: str) -> ColorMapType:
    """Return rio-tiler colormap."""
    return default_cmap.get(name)


def colormap(data: ImageData, colormap: ColorMapType) -> ImageData:
    """Apply colormap to ImageData."""
    return data.apply_colormap(colormap)
