"""titiler.openeo.processes indices."""

from .data_model import ImageData
from .math import normalized_difference

__all__ = ["ndvi"]


def ndvi(data: ImageData, nir: int, red: int):
    """Apply NDVI."""
    nirb = data.array[int(nir) - 1]
    redb = data.array[int(red) - 1]

    return ImageData(
        normalized_difference(nirb, redb),
        assets=data.assets,
        crs=data.crs,
        bounds=data.bounds,
        band_names=data.band_names,
    )
