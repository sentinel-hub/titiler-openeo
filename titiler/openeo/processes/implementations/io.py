"""titiler.openeo.processes."""

from typing import Dict, Optional, Union, Any

import numpy
from rio_tiler.models import ImageData

from .data_model import RasterStack

__all__ = ["save_result"]


def _save_single_result(
    data: Union[ImageData, numpy.ndarray, numpy.ma.MaskedArray],
    format: str,
    options: Optional[Dict] = None,
) -> bytes:
    """Save a single result (ImageData or numpy array)."""
    if isinstance(data, (numpy.ma.MaskedArray, numpy.ndarray)):
        data = ImageData(data)

    options = options or {}

    if format.lower() in ["jpeg", "jpg", "png"] and data.array.dtype != "uint8":
        data.array = data.array.astype("uint8")

    return data.render(img_format=format.lower(), **options)


def save_result(
    data: Union[ImageData, numpy.ndarray, numpy.ma.MaskedArray, RasterStack],
    format: str,
    options: Optional[Dict] = None,
) -> Union[bytes, Dict[str, bytes]]:
    """Save Result.

    Args:
        data: ImageData, numpy array, or RasterStack to save
        format: Output format (e.g., 'png', 'jpeg', 'tiff')
        options: Additional rendering options

    Returns:
        For single images: bytes of the rendered image
        For RasterStack: dictionary mapping keys to rendered image bytes
    """
    # If data is a RasterStack (dictionary), save each item
    if isinstance(data, dict):
        if data.__len__() == 1:
            # If there is only one item, save it as a single result
            return _save_single_result(list(data.values())[0], format, options)
        # TODO: Implement saving RasterStacks (e.g. geoTIFF with multiple bands)
        raise NotImplementedError("Saving RasterStacks is not yet implemented")

    # Otherwise, save as a single result
    return _save_single_result(data, format, options)
