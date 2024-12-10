"""titiler.openeo processes Math."""

import numpy

__all__ = [
    "normalized_difference",
    "clip",
    "linear_scale_range",
]


def normalized_difference(
    x: numpy.ma.MaskedArray, y: numpy.ma.MaskedArray
) -> numpy.ma.MaskedArray:
    """Normalized Diff."""
    return (x - y) / (x + y)


def clip(data: numpy.ma.MaskedArray, in_min, in_max):
    """Clip."""
    return numpy.ma.clip(data, in_min, in_max)


def linear_scale_range(
    data: numpy.ma.MaskedArray,
    in_min: float,
    in_max: float,
    out_min: float = 0.0,
    out_max: float = 1.0,
):
    """Linear rescaling."""
    minv, maxv = min(in_min, in_max), max(in_min, in_max)
    data = clip(data, minv, maxv)
    return ((data - in_min) / (in_max - in_min)) * (out_max - out_min) + out_min
