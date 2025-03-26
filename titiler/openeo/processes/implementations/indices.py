"""titiler.openeo.processes indices."""

from typing import Dict, Union

from .data_model import ImageData, RasterStack, to_raster_stack
from .math import normalized_difference

__all__ = ["ndvi"]


def _apply_ndvi(data: ImageData, nir: int, red: int) -> ImageData:
    """Apply NDVI to a single ImageData."""
    nirb = data.array[int(nir) - 1]
    redb = data.array[int(red) - 1]

    return ImageData(
        normalized_difference(nirb, redb),
        assets=data.assets,
        crs=data.crs,
        bounds=data.bounds,
        band_names=[
            "ndvi",
        ],
    )


def ndvi(
    data: RasterStack, nir: int, red: int
) -> RasterStack:
    """Apply NDVI to RasterStack.

    Args:
        data: RasterStack to process
        nir: Index of the NIR band (1-based)
        red: Index of the red band (1-based)

    Returns:
        RasterStack with NDVI results
    """
    # Apply NDVI to each item in the stack
    result: Dict[str, ImageData] = {}
    for key, img_data in data.items():
        result[key] = _apply_ndvi(img_data, nir, red)
    return result
