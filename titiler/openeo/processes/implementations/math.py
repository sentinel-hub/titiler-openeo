"""titiler.openeo processes Math."""

import builtins

import numpy

from .data_model import get_first_item, get_last_item
from .reduce import apply_pixel_selection

__all__ = [
    "absolute",
    "add",
    "arccos",
    "arcosh",
    "arcsin",
    "arctan",
    "arctan2",
    "arsinh",
    "artanh",
    "ceil",
    "clip",
    "constant",
    "cos",
    "cosh",
    "divide",
    "e",
    "exp",
    "first",
    "floor",
    "last",
    "linear_scale_range",
    "ln",
    "log",
    "min",
    "max",
    "mean",
    "median",
    "mod",
    "multiply",
    "normalized_difference",
    "pi",
    "power",
    "sd",
    "sgn",
    "sin",
    "sinh",
    "sqrt",
    "subtract",
    "tan",
    "tanh",
    "trunc",
    "variance",
]


def e():
    return numpy.e


def pi():
    return numpy.pi


def constant(x):
    return x


def divide(x, y):
    return x / y


def subtract(x, y):
    return x - y


def multiply(x, y):
    return x * y


def add(x, y):
    return x + y


def floor(x):
    return numpy.floor(x)


def ceil(x):
    return numpy.ceil(x)


def trunc(x):
    return numpy.trunc(x).astype(numpy.uint8)


def _round(x, p=0):
    return numpy.around(x, decimals=p)


def exp(p):
    return numpy.exp(p)


def log(x, base):
    return numpy.log(x) / numpy.log(base)


def ln(x):
    return numpy.log(x)


def cos(x):
    return numpy.cos(x)


def sin(x):
    return numpy.sin(x)


def tan(x):
    return numpy.tan(x)


def arccos(x):
    return numpy.arccos(x)


def arcsin(x):
    return numpy.arcsin(x)


def arctan(x):
    return numpy.arctan(x)


def cosh(x):
    return numpy.cosh(x)


def sinh(x):
    return numpy.sinh(x)


def tanh(x):
    return numpy.tanh(x)


def arcosh(x):
    return numpy.arccosh(x)


def arsinh(x):
    return numpy.arcsinh(x)


def artanh(x):
    return numpy.arctanh(x)


def arctan2(y, x):
    return numpy.arctan2(y, x)


def mod(x, y):
    return numpy.mod(x, y)


def absolute(x):
    return numpy.abs(x)


def sgn(x):
    return numpy.sign(x)


def sqrt(x):
    return numpy.sqrt(x)


def power(base, p):
    return base**p


def _min(data, ignore_nodata=True, axis=None, keepdims=False):
    if isinstance(data, numpy.ma.MaskedArray):
        if not ignore_nodata:
            # Fill masked values with the array's data
            data = data.filled(data.fill_value)
        return numpy.ma.min(data, axis=axis, keepdims=keepdims)

    mind = numpy.min(data, axis=axis, keepdims=keepdims)
    return mind


def _max(data, ignore_nodata=True, axis=None, keepdims=False):
    if isinstance(data, numpy.ma.MaskedArray):
        if not ignore_nodata:
            # Fill masked values with the array's data
            data = data.filled(data.fill_value)
        return numpy.ma.max(data, axis=axis, keepdims=keepdims)

    return numpy.max(data, axis=axis, keepdims=keepdims)


def median(data, axis=None, keepdims=False):
    if isinstance(data, numpy.ma.MaskedArray):
        return numpy.ma.median(data, axis=axis, keepdims=keepdims)

    return numpy.median(data, axis=axis, keepdims=keepdims)


def mean(data, axis=None, keepdims=False):
    if isinstance(data, numpy.ma.MaskedArray):
        return numpy.ma.mean(data, axis=axis, keepdims=keepdims)

    return numpy.mean(data, axis=axis, keepdims=keepdims)


def sd(x, axis=None, keepdims=False):
    return numpy.std(x, axis=axis, keepdims=keepdims, ddof=1)


def variance(x, axis=None, keepdims=False):
    return numpy.var(x, axis=axis, keepdims=keepdims, ddof=1)


def normalized_difference(x, y):
    return (x - y) / (x + y)


def clip(x, min, max):
    return numpy.clip(x, min, max)


def linear_scale_range(
    x,
    inputMin: float,
    inputMax: float,
    outputMin: float = 0.0,
    outputMax: float = 1.0,
):
    minv, maxv = builtins.min(inputMin, inputMax), builtins.max(inputMin, inputMax)
    x = clip(x, minv, maxv)
    return ((x - inputMin) / (inputMax - inputMin)) * (
        outputMax - outputMin
    ) + outputMin


def first(data):
    """Return the first element of the array."""
    # Handle RasterStack
    if isinstance(data, dict):
        # Check if data has timestamp-based grouping capability (LazyRasterStack)
        if hasattr(data, "timestamps") and hasattr(data, "get_by_timestamp"):
            timestamps = data.timestamps()
            if not timestamps:
                raise ValueError("No timestamps available for first operation")

            # Get the first timestamp and mosaic all images from that timestamp
            first_timestamp = builtins.min(timestamps)
            timestamp_group = data.get_by_timestamp(first_timestamp)

            if not timestamp_group:
                raise ValueError("No data available for first timestamp")

            # If there's only one image in the group, return its first band
            if len(timestamp_group) == 1:
                img = next(iter(timestamp_group.values()))
                return img.array[0]

            # Multiple images - apply pixel selection to create mosaic, then get first band
            mosaic_result = apply_pixel_selection(
                timestamp_group, pixel_selection="first"
            )
            mosaic_img = next(iter(mosaic_result.values()))
            return mosaic_img.array[0]
        else:
            # Regular RasterStack - use existing logic
            first_img = get_first_item(data)
            return first_img.array[0]
    elif isinstance(data, numpy.ndarray):
        return data[0]
    elif isinstance(data, numpy.ma.MaskedArray):
        return data[0].filled()
    else:
        raise TypeError("Unsupported data type for first function.")


def last(data):
    """Return the last element of the array."""
    # Handle RasterStack
    if isinstance(data, dict):
        # Check if data has timestamp-based grouping capability (LazyRasterStack)
        if hasattr(data, "timestamps") and hasattr(data, "get_by_timestamp"):
            timestamps = data.timestamps()
            if not timestamps:
                raise ValueError("No timestamps available for last operation")

            # Get the last timestamp and mosaic all images from that timestamp
            last_timestamp = builtins.max(timestamps)
            timestamp_group = data.get_by_timestamp(last_timestamp)

            if not timestamp_group:
                raise ValueError("No data available for last timestamp")

            # If there's only one image in the group, return its last band
            if len(timestamp_group) == 1:
                img = next(iter(timestamp_group.values()))
                return img.array[-1]

            # Multiple images - apply pixel selection to create mosaic, then get last band
            mosaic_result = apply_pixel_selection(
                timestamp_group, pixel_selection="first"
            )
            mosaic_img = next(iter(mosaic_result.values()))
            return mosaic_img.array[-1]
        else:
            # Regular RasterStack - use existing logic
            last_img = get_last_item(data)
            return last_img.array[-1]
    elif isinstance(data, numpy.ndarray):
        return data[-1]
    elif isinstance(data, numpy.ma.MaskedArray):
        return data[-1].filled()
    else:
        raise TypeError("Unsupported data type for last function.")


def max(data, ignore_nodata=True):
    """Return the maximum value of the array."""
    # Handle RasterStack
    if isinstance(data, dict):
        # Return a single array with the maximum value for each array items in the stack
        stacked_arrays = numpy.stack([v.array for v in data.values()], axis=0)
        return _max(stacked_arrays, ignore_nodata=ignore_nodata, axis=0)
    elif isinstance(data, numpy.ndarray):
        return _max(data, ignore_nodata=ignore_nodata)
    elif isinstance(data, numpy.ma.MaskedArray):
        return _max(data, ignore_nodata=ignore_nodata)
    else:
        raise TypeError("Unsupported data type for max function.")


def min(data, ignore_nodata=True):
    """Return the minimum value of the array."""
    # Handle RasterStack
    if isinstance(data, dict):
        # Return a single array with the minimum value for each array items in the stack
        stacked_arrays = numpy.stack([v.array for v in data.values()], axis=0)
        return _min(stacked_arrays, ignore_nodata=ignore_nodata, axis=0)
    elif isinstance(data, numpy.ndarray):
        return _min(data, ignore_nodata=ignore_nodata)
    elif isinstance(data, numpy.ma.MaskedArray):
        return _min(data, ignore_nodata=ignore_nodata)
    else:
        raise TypeError("Unsupported data type for min function.")
