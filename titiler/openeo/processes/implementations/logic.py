"""titiler.openeo.processes Logic and Comparison operations."""

from typing import Any, Optional, Tuple

import numpy
from openeo_pg_parser_networkx.pg_schema import Date, DateTime, Time, Year

__all__ = [
    "if_",
    "and_",
    "or_",
    "not_",
    "lt",
    "lte",
    "gt",
    "gte",
    "eq",
    "neq",
    "is_nodata",
]


def _unwrap_temporal(value: Any) -> Any:
    """Return the datetime inside a value that the graph parser made from a date string.

    The graph parser turns a date string such as ``"2024-06-03"`` into a
    ``Date``/``DateTime`` wrapper, which does not compare with a datetime.
    """
    if isinstance(value, (Year, Date, DateTime, Time)):
        return value.root
    return value


def _shapes_align(a: Tuple[int, ...], b: Tuple[int, ...]) -> bool:
    """Return True if same-length shapes ``a`` and ``b`` broadcast element-wise."""
    return all(x == y or x == 1 or y == 1 for x, y in zip(a, b))


def _align_to_reference(
    arr: numpy.ndarray, reference: Tuple[int, ...]
) -> numpy.ndarray:
    """Reshape ``arr`` so it broadcasts against the ``reference`` shape.

    numpy broadcasting aligns shapes on their *trailing* axes. In the datacube
    layout used here the spectral/band dimension is the *leading* axis while
    spatial dimensions are trailing, so a spectral-only vector such as ``(3,)``
    does not broadcast against a spectral cube such as ``(3, H, W)``. When an
    array's shape matches a leading slice of the reference rather than a
    trailing one, trailing singleton axes are inserted to line them up.
    """
    if arr.ndim == 0 or arr.ndim >= len(reference):
        return arr
    shape = arr.shape
    # Standard numpy alignment: the shape is a trailing slice of the reference.
    if _shapes_align(shape, reference[-arr.ndim :]):
        return arr
    # Leading alignment: the shape is a leading slice of the reference.
    if _shapes_align(shape, reference[: arr.ndim]):
        return arr.reshape(shape + (1,) * (len(reference) - arr.ndim))
    return arr


def if_(
    value: Any,
    accept: Any,
    reject: Optional[Any] = None,
) -> Any:
    """If-Then-Else conditional.

    If the value passed is `true`, returns the value of the `accept` parameter,
    otherwise returns the value of the `reject` parameter.

    Args:
        value: A boolean value (or null which is treated as false), can be array
        accept: A value that is returned if the boolean value is true
        reject: A value that is returned if the boolean value is not true.
                Defaults to None (null)

    Returns:
        Either the accept or reject argument depending on the given boolean value

    Examples:
        >>> if_(True, "A", "B")
        'A'
        >>> if_(False, "A", "B")
        'B'
        >>> if_(None, "A", "B")
        'B'
        >>> if_(True, 123)
        123
        >>> if_(False, 1) is None
        True
        >>> import numpy as np
        >>> if_(np.array([True, False, True]), 1, 0)
        array([1, 0, 1])
    """
    # Handle numpy arrays - use element-wise conditional
    if isinstance(value, numpy.ndarray):
        # Use numpy.where for element-wise if-then-else
        reject_val = reject if reject is not None else 0
        accept_arr = numpy.asanyarray(accept)
        reject_arr = numpy.asanyarray(reject_val)
        # numpy.where aligns operands on their trailing axes. Operands may
        # instead carry a leading spectral/band dimension (e.g. a constant
        # ``(bands,)`` vector selected against a ``(bands, H, W)`` cube), so
        # align every operand against the highest-rank one before selecting.
        reference = max((value, accept_arr, reject_arr), key=lambda a: a.ndim).shape
        return numpy.where(
            _align_to_reference(value, reference),
            _align_to_reference(accept_arr, reference),
            _align_to_reference(reject_arr, reference),
        )

    # Handle scalar boolean values
    # Return accept if value is exactly True, otherwise return reject
    # Note: null/None is treated as false
    if value is True:
        return accept
    return reject


# Set the process name to match the JSON specification
if_.__name__ = "if"


def and_(x: Any, y: Any) -> Any:
    """Logical AND operation.

    Args:
        x: First boolean value or array
        y: Second boolean value or array

    Returns:
        True if both x and y are true, False otherwise

    Examples:
        >>> and_(True, True)
        True
        >>> and_(True, False)
        False
        >>> and_(False, True)
        False
        >>> and_(False, False)
        False
        >>> import numpy as np
        >>> and_(np.array([True, False]), np.array([True, True]))
        array([ True, False])
    """
    # Handle numpy arrays - use element-wise logical AND
    if isinstance(x, numpy.ndarray) or isinstance(y, numpy.ndarray):
        return numpy.logical_and(x, y)

    # Handle scalar boolean values
    return bool(x) and bool(y)


and_.__name__ = "and"


def or_(x: Any, y: Any) -> Any:
    """Logical OR operation.

    Args:
        x: First boolean value or array
        y: Second boolean value or array

    Returns:
        True if at least one of x and y is true, False otherwise

    Examples:
        >>> or_(True, True)
        True
        >>> or_(True, False)
        True
        >>> or_(False, True)
        True
        >>> or_(False, False)
        False
        >>> import numpy as np
        >>> or_(np.array([True, False]), np.array([False, False]))
        array([ True, False])
    """
    # Handle numpy arrays - use element-wise logical OR
    if isinstance(x, numpy.ndarray) or isinstance(y, numpy.ndarray):
        return numpy.logical_or(x, y)

    # Handle scalar boolean values
    return bool(x) or bool(y)


or_.__name__ = "or"


def not_(x: Any) -> Any:
    """Logical NOT operation.

    No-data values are passed through: ``None`` gives ``None``, and masked
    array elements stay masked.

    Args:
        x: Boolean value or array to invert

    Returns:
        The inverted boolean value or array

    Examples:
        >>> not_(True)
        False
        >>> not_(False)
        True
        >>> not_(None) is None
        True
        >>> import numpy as np
        >>> not_(np.array([True, False]))
        array([False,  True])
    """
    if x is None:
        return None
    if isinstance(x, numpy.ndarray):
        return numpy.logical_not(x)
    return not bool(x)


not_.__name__ = "not"


def lt(x: Any, y: Any) -> bool:
    """Less than comparison.

    Args:
        x: First value to compare
        y: Second value to compare

    Returns:
        True if x is less than y, False otherwise

    Examples:
        >>> lt(1, 2)
        True
        >>> lt(2, 1)
        False
        >>> lt(1, 1)
        False
    """
    return _unwrap_temporal(x) < _unwrap_temporal(y)


def lte(x: Any, y: Any) -> bool:
    """Less than or equal comparison.

    Args:
        x: First value to compare
        y: Second value to compare
    Returns:
        True if x is less than or equal to y, False otherwise
    Examples:
        >>> lte(1, 2)
        True
        >>> lte(2, 1)
        False
        >>> lte(1, 1)
        True
    """
    return _unwrap_temporal(x) <= _unwrap_temporal(y)


def gt(x: Any, y: Any) -> bool:
    """Greater than comparison.

    Args:
        x: First value to compare
        y: Second value to compare

    Returns:
        True if x is greater than y, False otherwise

    Examples:
        >>> gt(2, 1)
        True
        >>> gt(1, 2)
        False
        >>> gt(1, 1)
        False
    """
    return _unwrap_temporal(x) > _unwrap_temporal(y)


def gte(x: Any, y: Any) -> bool:
    """Greater than or equal comparison.

    Args:
        x: First value to compare
        y: Second value to compare

    Returns:
        True if x is greater than or equal to y, False otherwise

    Examples:
        >>> gte(2, 1)
        True
        >>> gte(1, 2)
        False
        >>> gte(1, 1)
        True
    """
    return _unwrap_temporal(x) >= _unwrap_temporal(y)


def eq(x: Any, y: Any) -> bool:
    """Equality comparison.

    Args:
        x: First value to compare
        y: Second value to compare

    Returns:
        True if x is equal to y, False otherwise

    Examples:
        >>> eq(1, 1)
        True
        >>> eq(1, 2)
        False
        >>> eq("a", "a")
        True
        >>> eq("a", "b")
        False
    """
    return _unwrap_temporal(x) == _unwrap_temporal(y)


def neq(x: Any, y: Any) -> bool:
    """Inequality comparison.

    Args:
        x: First value to compare
        y: Second value to compare

    Returns:
        True if x is not equal to y, False otherwise

    Examples:
        >>> neq(1, 2)
        True
        >>> neq(1, 1)
        False
        >>> neq("a", "b")
        True
        >>> neq("a", "a")
        False
    """
    return _unwrap_temporal(x) != _unwrap_temporal(y)


def is_nodata(x: Any) -> Any:
    """Check whether the data is a no-data value.

    A ``null`` value and a masked value are no-data. For an array, the check is
    done element by element from the array mask, so the result is a boolean
    array of the same shape. ``NaN`` is not no-data unless it is masked.

    Args:
        x: The data to check

    Returns:
        True if the data is a no-data value, False otherwise

    Examples:
        >>> is_nodata(1)
        False
        >>> is_nodata("Test")
        False
        >>> is_nodata(None)
        True
        >>> is_nodata([None, None])
        False
        >>> import numpy as np
        >>> is_nodata(np.ma.masked_array([1, 2], mask=[True, False]))
        array([ True, False])
    """
    if x is None or x is numpy.ma.masked:
        return True
    if isinstance(x, numpy.ma.MaskedArray):
        return numpy.ma.getmaskarray(x).copy()
    if isinstance(x, numpy.ndarray):
        return numpy.zeros(x.shape, dtype=bool)
    return False
