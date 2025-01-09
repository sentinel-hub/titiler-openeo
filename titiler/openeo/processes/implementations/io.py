"""titiler.openeo.processes."""

from typing import Dict, Optional, Union

import numpy
from rio_tiler.models import ImageData

__all__ = ["save_result"]


def save_result(
    data: Union[ImageData, numpy.ndarray, numpy.ma.MaskedArray],
    format: str,
    options: Optional[Dict] = None,
) -> bytes:
    """Save Result."""
    if isinstance(data, (numpy.ma.MaskedArray, numpy.ndarray)):
        data = ImageData(data)

    options = options or {}

    if format.lower() in ["jpeg", "jpg", "png"] and data.array.dtype != "uint8":
        data.array = data.array.astype("uint8")

    return data.render(img_format=format.lower(), **options)
