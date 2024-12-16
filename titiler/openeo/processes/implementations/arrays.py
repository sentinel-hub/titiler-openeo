"""titiler.processes.implementations arrays."""

import numpy
from numpy.typing import ArrayLike

__all__ = ["array_element"]


def array_element(data: ArrayLike, index: int):
    """Return element from array."""
    if index is not None and index < 0:
        raise IndexError(f"Index value must be >= 0, {index}")

    return numpy.take(data, index, axis=0)
