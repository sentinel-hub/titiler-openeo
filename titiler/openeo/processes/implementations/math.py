"""titiler.openeo processes Math."""

import numpy

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


def _min(x, axis=None, keepdims=False):
    return numpy.min(x, axis=axis, keepdims=keepdims)


def _max(x, axis=None, keepdims=False):
    return numpy.max(x, axis=axis, keepdims=keepdims)


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


def clip(x, in_min, in_max):
    return numpy.clip(x, in_min, in_max)


def linear_scale_range(
    x,
    inputMin: float,
    inputMax: float,
    outputMin: float = 0.0,
    outputMax: float = 1.0,
):
    minv, maxv = min(inputMin, inputMax), max(inputMin, inputMax)
    x = clip(x, minv, maxv)
    return ((x - inputMin) / (inputMax - inputMin)) * (
        outputMax - outputMin
    ) + outputMin

def first(data):
    """Return the first element of the array."""
    # Handle RasterStack
    if isinstance(data, dict):
        first_elements = {k: v.array[0] for k, v in data.items()}
        # return a multi-dimensional array
        return numpy.stack(list(first_elements.values()), axis=0)
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
        last_elements = {k: v.array[-1] for k, v in data.items()}
        # return a multi-dimensional array
        return numpy.stack(list(last_elements.values()), axis=0)
    elif isinstance(data, numpy.ndarray):
        return data[-1]
    elif isinstance(data, numpy.ma.MaskedArray):
        return data[-1].filled()
    else:
        raise TypeError("Unsupported data type for last function.")
    
