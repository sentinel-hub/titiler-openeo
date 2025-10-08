"""titiler.openeo.processes Logic and Comparison operations."""

from typing import Any, Optional

__all__ = ["if_"]


def if_(
    value: Optional[bool],
    accept: Any,
    reject: Optional[Any] = None,
) -> Any:
    """If-Then-Else conditional.

    If the value passed is `true`, returns the value of the `accept` parameter,
    otherwise returns the value of the `reject` parameter.

    Args:
        value: A boolean value (or null which is treated as false)
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
        >>> if_(False, 1)
        None
    """
    # Return accept if value is exactly True, otherwise return reject
    # Note: null/None is treated as false
    if value is True:
        return accept
    return reject


# Set the process name to match the JSON specification
if_.__name__ = "if"
