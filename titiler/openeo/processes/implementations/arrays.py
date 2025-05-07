"""titiler.processes.implementations arrays."""

from typing import Optional, Union

import numpy
from numpy.typing import ArrayLike
from rio_tiler.models import ImageData

from .data_model import LazyRasterStack, RasterStack

__all__ = ["array_element", "to_image"]


def array_element(
    data: Union[ArrayLike, RasterStack, LazyRasterStack],
    index: Optional[int] = None,
    label: Optional[str] = None,
) -> ArrayLike:
    """Return element from array.

    Args:
        data: Array or RasterStack to extract element from
        index: Index of the element to extract
        label: Label of the element to extract (for RasterStack)

    Returns:
        The element at the specified index
    """
    if index is not None and index < 0:
        raise IndexError(f"Index value must be >= 0, {index}")
    if label is not None and label not in data.keys():
        raise KeyError(f"Label {label} not found in data: {data.keys()}")
    if index is None and label is None:
        raise ValueError("Either index or label must be provided")

    # Handle RasterStack
    if isinstance(data, dict):
        if index is not None:
            array_dict = {
                k: numpy.take(v.array, index, axis=0) for k, v in data.items()
            }
        if label is not None:
            array_dict = {k: v.array for k, v in data.items() if k == label}
        # return a multi-dimensional array
        return numpy.stack(list(array_dict.values()), axis=0)

    # Handle regular arrays
    elif isinstance(data, ImageData):
        return numpy.take(data.array, index, axis=0)
    else:
        return numpy.take(data, index, axis=0)


def to_image(data: Union[numpy.ndarray, numpy.ma.MaskedArray]) -> RasterStack:
    """Create a RasterStack from an array."""
    return {"data": ImageData(data)}
