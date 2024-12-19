"""titiler.processes.implementations arrays."""

import numpy
from numpy.typing import ArrayLike

from .data_model import ImageData

__all__ = ["array_element", "to_image"]


def array_element(data: ArrayLike, index: int):
    """Return element from array."""
    if index is not None and index < 0:
        raise IndexError(f"Index value must be >= 0, {index}")

    return numpy.take(data, index, axis=0)


def to_image(data: ArrayLike) -> ImageData:
    """Create an ImageData object from an array."""
    return ImageData(data)
