"""titiler.openeo.processes indices."""

from typing import Dict

from .data_model import ImageData, RasterStack
from .math import normalized_difference

__all__ = ["ndvi", "ndwi"]


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


def _apply_ndwi(data: ImageData, nir: int, swir: int) -> ImageData:
    """Apply NDWI to a single ImageData."""
    nirb = data.array[int(nir) - 1]
    swirb = data.array[int(swir) - 1]

    return ImageData(
        normalized_difference(nirb, swirb),
        assets=data.assets,
        crs=data.crs,
        bounds=data.bounds,
        band_names=[
            "ndwi",
        ],
    )

def ndwi(data: RasterStack, nir: int, swir: int) -> RasterStack:
    """Apply NDWI to RasterStack.

    Args:
        data: RasterStack to process
        nir: Index of the NIR band (1-based)
        swir: Index of the SWIR band (1-based)

    Returns:
        RasterStack with NDWI results
    """
    # Apply NDWI to each item in the stack
    result: Dict[str, ImageData] = {}
    for key, img_data in data.items():
        result[key] = _apply_ndwi(img_data, nir, swir)
    return result

def ndvi(data: RasterStack, nir: int, red: int) -> RasterStack:
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
