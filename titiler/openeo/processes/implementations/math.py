"""titiler.openeo processes Math."""

import builtins
import functools

import numpy

from .data_model import RasterStack
from .reduce import apply_pixel_selection


def _promote(a):
    """Promote integer/boolean inputs to float32; leave everything else as-is.

    numpy resolves integer inputs to float64 for division and transcendental
    functions. For raster math that produces an index/derived cube, float32 is
    ample and halves the result. Source rasters stay at their compact integer
    dtype (we only convert at the point of a float-producing op), and existing
    floating inputs keep their precision. Masks are preserved by ``astype``.

    Only integer/boolean dtypes are converted — other dtypes (float, complex,
    datetime, object) are left untouched. Python ``int``/``bool`` scalars are
    promoted too, so a scalar operand (e.g. ``log(x, base=10)``) doesn't pull the
    result back to float64.
    """
    dtype = getattr(a, "dtype", None)
    if dtype is not None:
        if numpy.issubdtype(dtype, numpy.integer) or numpy.issubdtype(
            dtype, numpy.bool_
        ):
            return a.astype("float32")
        return a
    # Python scalars: promote int (incl. bool); leave float/other untouched so
    # they don't upcast a float32 array.
    if isinstance(a, int):
        return numpy.float32(a)
    return a


def _float32_for_integers(func):
    """Decorator: promote integer array arguments to float32 before calling.

    Applied to element-wise ops that already convert integer input to float64
    (divide, transcendental functions, normalized_difference, ...), so their
    result is float32 instead. Only narrows existing float64 results; never
    changes an integer-only result, since those ops are not decorated.
    """

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        return func(
            *(_promote(a) for a in args),
            **{k: _promote(v) for k, v in kwargs.items()},
        )

    return wrapper


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
    "count",
    "divide",
    "e",
    "exp",
    "first",
    "firstpixel",
    "floor",
    "highestpixel",
    "last",
    "lastbandhight",
    "lastbandlow",
    "linear_scale_range",
    "ln",
    "log",
    "lowestpixel",
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
    "stdev",
    "subtract",
    "tan",
    "tanh",
    "trunc",
    "variance",
]

# Internal pixel selection reducers - not exported as openEO processes
# but used by _reduce_temporal_dimension for performance optimization
# These are detected by their __name__ attribute in reduce.py
#
# NOTE: 'firstpixel' is different from 'first':
# - first/last = first/last item in temporal order (from math.py, openEO standard)
# - firstpixel = first available (non-masked) pixel value (rio-tiler)
__pixel_selection_reducers__ = [
    "count",
    "firstpixel",
    "highestpixel",
    "lastbandhight",
    "lastbandlow",
    "lowestpixel",
    "stdev",
]


def e():
    return numpy.e


def pi():
    return numpy.pi


def constant(x):
    return x


@_float32_for_integers
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


@_float32_for_integers
def exp(p):
    return numpy.exp(p)


@_float32_for_integers
def log(x, base):
    return numpy.log(x) / numpy.log(base)


@_float32_for_integers
def ln(x):
    return numpy.log(x)


@_float32_for_integers
def cos(x):
    return numpy.cos(x)


@_float32_for_integers
def sin(x):
    return numpy.sin(x)


@_float32_for_integers
def tan(x):
    return numpy.tan(x)


@_float32_for_integers
def arccos(x):
    return numpy.arccos(x)


@_float32_for_integers
def arcsin(x):
    return numpy.arcsin(x)


@_float32_for_integers
def arctan(x):
    return numpy.arctan(x)


@_float32_for_integers
def cosh(x):
    return numpy.cosh(x)


@_float32_for_integers
def sinh(x):
    return numpy.sinh(x)


@_float32_for_integers
def tanh(x):
    return numpy.tanh(x)


@_float32_for_integers
def arcosh(x):
    return numpy.arccosh(x)


@_float32_for_integers
def arsinh(x):
    return numpy.arcsinh(x)


@_float32_for_integers
def artanh(x):
    return numpy.arctanh(x)


@_float32_for_integers
def arctan2(y, x):
    return numpy.arctan2(y, x)


def mod(x, y):
    return numpy.mod(x, y)


def absolute(x):
    return numpy.abs(x)


def sgn(x):
    return numpy.sign(x)


@_float32_for_integers
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


def median(data, axis=0, keepdims=False):
    """Calculate median across the data.

    Reduces over axis 0 (the leading "array" dimension) by default, consistent
    with the other aggregators — see the note in max(). When used with
    RasterStack, it uses the efficient streaming approach.
    """
    # Handle RasterStack - use apply_pixel_selection for efficiency
    if isinstance(data, RasterStack):
        result = apply_pixel_selection(data, pixel_selection="median")
        return result.first.array
    elif isinstance(data, numpy.ma.MaskedArray):
        return numpy.ma.median(data, axis=axis, keepdims=keepdims)

    return numpy.median(data, axis=axis, keepdims=keepdims)


def mean(data, axis=0, keepdims=False):
    """Calculate mean across the data.

    Reduces over axis 0 (the leading "array" dimension) by default, consistent
    with the other aggregators — see the note in max(). When used with
    RasterStack, it uses the efficient streaming approach.
    """
    # Handle RasterStack - use apply_pixel_selection for efficiency
    if isinstance(data, RasterStack):
        result = apply_pixel_selection(data, pixel_selection="mean")
        return result.first.array
    elif isinstance(data, numpy.ma.MaskedArray):
        return numpy.ma.mean(data, axis=axis, keepdims=keepdims)

    return numpy.mean(data, axis=axis, keepdims=keepdims)


def sd(x, axis=0, keepdims=False):
    return numpy.std(x, axis=axis, keepdims=keepdims, ddof=1)


def stdev(data, axis=0, keepdims=False):
    """Calculate standard deviation across the data.

    Reduces over axis 0 (the leading "array" dimension) by default, consistent
    with the other aggregators — see the note in max(). This is an alias for
    sd() that matches the PixelSelectionMethod naming. When used with
    RasterStack, it uses the efficient streaming approach.
    """
    # Handle RasterStack - use apply_pixel_selection for efficiency
    if isinstance(data, RasterStack):
        result = apply_pixel_selection(data, pixel_selection="stdev")
        return result.first.array
    return numpy.std(data, axis=axis, keepdims=keepdims, ddof=1)


def variance(x, axis=0, keepdims=False):
    return numpy.var(x, axis=axis, keepdims=keepdims, ddof=1)


def count(data, condition=None):
    """Count valid (non-masked) pixels across the data.

    When used with RasterStack, it counts valid pixels using the efficient
    streaming approach via PixelSelectionMethod.

    Args:
        data: Input data (array or RasterStack)
        condition: Optional condition (not used for RasterStack pixel counting)

    Returns:
        Array with count of valid pixels
    """
    # Handle RasterStack - use apply_pixel_selection for efficiency
    if isinstance(data, RasterStack):
        result = apply_pixel_selection(data, pixel_selection="count")
        return result.first.array
    elif isinstance(data, numpy.ma.MaskedArray):
        # Count non-masked values
        if condition is not None:
            return numpy.ma.sum(condition, axis=0)
        return numpy.ma.sum(~data.mask, axis=0)
    elif isinstance(data, numpy.ndarray):
        if condition is not None:
            return numpy.sum(condition, axis=0)
        return numpy.full(
            data.shape[1:] if data.ndim > 1 else data.shape,
            data.shape[0] if data.ndim > 1 else 1,
        )
    else:
        raise TypeError("Unsupported data type for count function.")


@_float32_for_integers
def normalized_difference(x, y):
    return (x - y) / (x + y)


def clip(x, min, max):
    return numpy.clip(x, min, max)


@_float32_for_integers
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
    # Handle RasterStack - use .first property for efficient single-item access
    if isinstance(data, RasterStack):
        return data.first.array
    elif isinstance(data, numpy.ndarray):
        return data[0]
    elif isinstance(data, numpy.ma.MaskedArray):
        return data[0].filled()
    else:
        raise TypeError("Unsupported data type for first function.")


def last(data):
    """Return the last element of the array."""
    # Handle RasterStack - use .last property for efficient single-item access
    if isinstance(data, RasterStack):
        return data.last.array
    elif isinstance(data, numpy.ndarray):
        return data[-1]
    elif isinstance(data, numpy.ma.MaskedArray):
        return data[-1].filled()
    else:
        raise TypeError("Unsupported data type for last function.")


def max(data, ignore_nodata: bool = True):
    """Return the maximum value of the array.

    ``ignore_nodata`` is a boolean flag, annotated so the @process validator
    rejects a non-boolean (e.g. an array) with a clear error instead of a cryptic
    numpy "truth value of an array is ambiguous". This catches the common misuse
    ``max(a, b)`` — openEO ``max`` is an array aggregator, so the second
    positional ``b`` binds to ``ignore_nodata``; element-wise max of two bands is
    ``max(array_create([a, b]))``.
    """
    # Handle RasterStack
    if isinstance(data, RasterStack):
        # Return a single array with the maximum value for each array items in the stack.
        # numpy.ma.stack (not numpy.stack) preserves the per-slice nodata masks; plain
        # numpy.stack silently drops them, turning masked nodata into valid values.
        stacked_arrays = numpy.ma.stack([v.array for v in data.values()], axis=0)
        return _max(stacked_arrays, ignore_nodata=ignore_nodata, axis=0)
    elif isinstance(data, numpy.ndarray):
        # Reduce over axis 0 (the leading "array" dimension), NOT globally. In
        # titiler's vectorized cube model the leading axis is the dimension an
        # openEO aggregator operates on (the time axis in apply_dimension(t)
        # callbacks, the band axis in reduce_dimension(bands)). This mirrors the
        # RasterStack branch above and avoids collapsing every spatial pixel into
        # a single scalar. (MaskedArray is an ndarray subclass, so it lands here.)
        return _max(data, ignore_nodata=ignore_nodata, axis=0)
    else:
        raise TypeError("Unsupported data type for max function.")


def min(data, ignore_nodata: bool = True):
    """Return the minimum value of the array.

    ``ignore_nodata`` is annotated as a boolean so the @process validator rejects
    a non-boolean (e.g. an array) with a clear error rather than a cryptic numpy
    crash — see the note in max().
    """
    # Handle RasterStack
    if isinstance(data, RasterStack):
        # Return a single array with the minimum value for each array items in the stack.
        # numpy.ma.stack (not numpy.stack) preserves the per-slice nodata masks; plain
        # numpy.stack silently drops them, turning masked nodata into valid values.
        stacked_arrays = numpy.ma.stack([v.array for v in data.values()], axis=0)
        return _min(stacked_arrays, ignore_nodata=ignore_nodata, axis=0)
    elif isinstance(data, numpy.ndarray):
        # Reduce over axis 0 (the leading "array" dimension), NOT globally — see
        # the note in max(). (MaskedArray is an ndarray subclass, so it lands here.)
        return _min(data, ignore_nodata=ignore_nodata, axis=0)
    else:
        raise TypeError("Unsupported data type for min function.")


def highestpixel(data):
    """Select the highest pixel values across the temporal dimension.

    This uses the PixelSelectionMethod 'highest' which feeds the mosaic array
    with the highest pixel values. When used with RasterStack, it processes
    data incrementally using the efficient streaming approach.

    Args:
        data: Input data (array or RasterStack)

    Returns:
        Array with the highest pixel values
    """
    # Handle RasterStack - use apply_pixel_selection for efficiency
    if isinstance(data, RasterStack):
        result = apply_pixel_selection(data, pixel_selection="highest")
        return result.first.array
    elif isinstance(data, numpy.ndarray) or isinstance(data, numpy.ma.MaskedArray):
        # For arrays, return max along first axis (temporal)
        return _max(data, ignore_nodata=True, axis=0)
    else:
        raise TypeError("Unsupported data type for highest function.")


def lowestpixel(data):
    """Select the lowest pixel values across the temporal dimension.

    This uses the PixelSelectionMethod 'lowest' which feeds the mosaic array
    with the lowest pixel values. When used with RasterStack, it processes
    data incrementally using the efficient streaming approach.

    Args:
        data: Input data (array or RasterStack)

    Returns:
        Array with the lowest pixel values
    """
    # Handle RasterStack - use apply_pixel_selection for efficiency
    if isinstance(data, RasterStack):
        result = apply_pixel_selection(data, pixel_selection="lowest")
        return result.first.array
    elif isinstance(data, numpy.ndarray) or isinstance(data, numpy.ma.MaskedArray):
        # For arrays, return min along first axis (temporal)
        return _min(data, ignore_nodata=True, axis=0)
    else:
        raise TypeError("Unsupported data type for lowest function.")


def lastbandlow(data):
    """Select pixels using the last band as decision factor (lowest value).

    This uses the PixelSelectionMethod 'lastbandlow' which feeds the mosaic
    array using the last band as the decision factor, selecting pixels where
    the last band has the lowest value.

    Args:
        data: Input data (array or RasterStack)

    Returns:
        Array with pixel selection based on lowest last band value
    """
    # Handle RasterStack - use apply_pixel_selection for efficiency
    if isinstance(data, RasterStack):
        result = apply_pixel_selection(data, pixel_selection="lastbandlow")
        return result.first.array
    elif isinstance(data, numpy.ndarray) or isinstance(data, numpy.ma.MaskedArray):
        # For arrays, find indices where last band is minimum and select those pixels
        if data.ndim < 2:
            return data
        # Get the last band values and find minimum along temporal axis
        last_band = data[:, -1] if data.ndim == 3 else data[-1]
        min_idx = numpy.argmin(last_band, axis=0)
        # Select pixels based on minimum last band
        if data.ndim == 3:
            result = numpy.take_along_axis(
                data, min_idx[numpy.newaxis, numpy.newaxis, :, :], axis=0
            )
            return result.squeeze(axis=0)
        return data[min_idx]
    else:
        raise TypeError("Unsupported data type for lastbandlow function.")


def lastbandhight(data):
    """Select pixels using the last band as decision factor (highest value).

    This uses the PixelSelectionMethod 'lastbandhight' which feeds the mosaic
    array using the last band as the decision factor, selecting pixels where
    the last band has the highest value.

    Note: The spelling 'lastbandhight' matches rio_tiler's PixelSelectionMethod.

    Args:
        data: Input data (array or RasterStack)

    Returns:
        Array with pixel selection based on highest last band value
    """
    # Handle RasterStack - use apply_pixel_selection for efficiency
    if isinstance(data, RasterStack):
        result = apply_pixel_selection(data, pixel_selection="lastbandhight")
        return result.first.array
    elif isinstance(data, numpy.ndarray) or isinstance(data, numpy.ma.MaskedArray):
        # For arrays, find indices where last band is maximum and select those pixels
        if data.ndim < 2:
            return data
        # Get the last band values and find maximum along temporal axis
        last_band = data[:, -1] if data.ndim == 3 else data[-1]
        max_idx = numpy.argmax(last_band, axis=0)
        # Select pixels based on maximum last band
        if data.ndim == 3:
            result = numpy.take_along_axis(
                data, max_idx[numpy.newaxis, numpy.newaxis, :, :], axis=0
            )
            return result.squeeze(axis=0)
        return data[max_idx]
    else:
        raise TypeError("Unsupported data type for lastbandhight function.")


def firstpixel(data):
    """Select the first available (non-masked) pixel value across the temporal dimension.

    This uses the PixelSelectionMethod 'first' which feeds the mosaic array
    with the first valid pixel value encountered. This is useful for filling
    in masked/nodata areas with valid pixels from other timestamps.

    NOTE: This is different from math.py's `first()` function:
    - first() = returns data from the first timestamp (chronologically)
    - firstpixel() = returns first valid pixel value (fills masked areas)

    Args:
        data: Input data (array or RasterStack)

    Returns:
        Array with first available pixel values
    """
    # Handle RasterStack - use apply_pixel_selection for efficiency
    if isinstance(data, RasterStack):
        result = apply_pixel_selection(data, pixel_selection="first")
        return result.first.array
    elif isinstance(data, numpy.ma.MaskedArray):
        # For masked arrays, iterate through temporal axis and pick first valid
        if data.ndim < 2:
            return data
        # Create output array with first valid values
        result = numpy.ma.array(numpy.zeros_like(data[0]), mask=True)
        for i in range(data.shape[0]):
            # Fill in masked positions with valid values from this slice
            fill_mask = result.mask & ~data[i].mask
            result = numpy.ma.where(fill_mask, data[i], result)
            # Update mask - position is unmasked if we found a valid value
            result.mask = result.mask & data[i].mask
            if not numpy.any(result.mask):
                break
        return result
    elif isinstance(data, numpy.ndarray):
        # For regular arrays, just return the first element (no masking)
        return data[0]
    else:
        raise TypeError("Unsupported data type for firstpixel function.")
