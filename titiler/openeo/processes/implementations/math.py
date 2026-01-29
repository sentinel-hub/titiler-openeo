"""titiler.openeo processes Math."""

import builtins

import numpy

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


def stdev(data, axis=None, keepdims=False):
    """Calculate standard deviation across the data.

    This is an alias for sd() that matches the PixelSelectionMethod naming.
    When used with RasterStack, it uses the efficient streaming approach.
    """
    # Handle RasterStack - use apply_pixel_selection for efficiency
    if isinstance(data, dict):
        result = apply_pixel_selection(data, pixel_selection="stdev")
        return result["data"].array
    return numpy.std(data, axis=axis, keepdims=keepdims, ddof=1)


def variance(x, axis=None, keepdims=False):
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
    if isinstance(data, dict):
        result = apply_pixel_selection(data, pixel_selection="count")
        return result["data"].array
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
            first_img = next(iter(data.values()))
            return first_img.array
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
            last_img = list(data.values())[-1]
            return last_img.array
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
    if isinstance(data, dict):
        result = apply_pixel_selection(data, pixel_selection="highest")
        return result["data"].array
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
    if isinstance(data, dict):
        result = apply_pixel_selection(data, pixel_selection="lowest")
        return result["data"].array
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
    if isinstance(data, dict):
        result = apply_pixel_selection(data, pixel_selection="lastbandlow")
        return result["data"].array
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
    if isinstance(data, dict):
        result = apply_pixel_selection(data, pixel_selection="lastbandhight")
        return result["data"].array
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
    if isinstance(data, dict):
        result = apply_pixel_selection(data, pixel_selection="first")
        return result["data"].array
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
