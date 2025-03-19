"""titiler.processes.implementations arrays."""

from typing import Union

import numpy
from numpy.typing import ArrayLike
from rio_tiler.models import ImageData

from .data_model import LazyRasterStack, RasterStack

__all__ = ["array_element", "to_image"]


def array_element(
    data: Union[ArrayLike, ImageData, RasterStack, LazyRasterStack], index: int
):
    """Return element from array.

    Args:
        data: Array, ImageData, or RasterStack to extract element from
        index: Index of the element to extract

    Returns:
        The element at the specified index
    """
    if index is not None and index < 0:
        raise IndexError(f"Index value must be >= 0, {index}")

    # Handle ImageData
    if isinstance(data, ImageData):
        return numpy.take(data.array, index, axis=0)

    # Handle RasterStack and LazyRasterStack
    elif isinstance(data, dict):
        array_dict = {k: numpy.take(v.array, index, axis=0) for k, v in data.items()}
        # return a multi-dimensional array
        return numpy.stack(list(array_dict.values()), axis=0)

    # Handle regular arrays
    else:
        return numpy.take(data, index, axis=0)


def to_image(data: Union[numpy.ndarray, numpy.ma.MaskedArray]) -> ImageData:
    """Create an ImageData object from an array."""
    return ImageData(data)
