"""titiler.openeo.processes Logic and Comparison operations."""

from typing import Any, Optional, Tuple

import numpy

__all__ = ["if_", "and_", "or_", "lt", "lte", "gt", "gte", "eq", "neq"]


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
    return x < y


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
    return x <= y


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
    return x > y


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
    return x >= y


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
    return x == y


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
    return x != y
