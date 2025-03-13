"""titiler.openeo.processes indices."""

from typing import Dict, Union

from .data_model import ImageData, RasterStack
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
    data: Union[ImageData, RasterStack], nir: int, red: int
) -> Union[ImageData, RasterStack]:
    """Apply NDVI to ImageData or RasterStack.

    Args:
        data: ImageData or RasterStack to process
        nir: Index of the NIR band (1-based)
        red: Index of the red band (1-based)

    Returns:
        ImageData or RasterStack with NDVI results
    """
    # If data is a single ImageData, apply NDVI directly
    if isinstance(data, ImageData):
        return _apply_ndvi(data, nir, red)

    # If data is a RasterStack (dictionary), apply NDVI to each item
    if isinstance(data, dict):
        result: Dict[str, ImageData] = {}
        for key, img_data in data.items():
            result[key] = _apply_ndvi(img_data, nir, red)
        return result

    # If we get here, data is neither ImageData nor a dictionary
    raise TypeError(f"Expected ImageData or RasterStack, got {type(data)}")
