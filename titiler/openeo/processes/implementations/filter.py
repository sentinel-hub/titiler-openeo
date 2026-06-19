"""titiler.openeo filter processes."""

from typing import List, Optional

from .data_model import RasterStack
from .reduce import DimensionNotAvailable, _parse_intervals, _timestamp_in_interval

__all__ = ["filter_temporal"]


def filter_temporal(
    data: RasterStack,
    extent: List[Optional[str]],
    dimension: Optional[str] = None,
) -> RasterStack:
    """Limits the data cube to the specified interval of dates and/or times.

    The filter checks whether each of the temporal dimension labels is greater
    than or equal to the lower boundary (start date/time) and less than the
    upper boundary (end date/time). This corresponds to a left-closed interval,
    which contains the lower boundary but not the upper boundary.

    Args:
        data: A data cube with a temporal dimension.
        extent: Left-closed temporal interval, i.e. an array with exactly two
            elements. The first element is the (included) start, the second the
            (excluded) end. RFC 3339 date-time or date strings. One boundary may
            be ``None`` for an unbounded interval, but never both.
        dimension: The name of the temporal dimension to filter on. If ``None``,
            the filter applies to the (single) temporal dimension.

    Returns:
        A data cube restricted to the specified temporal extent. The temporal
        dimension may have fewer labels; all other properties remain unchanged.

    Raises:
        TemporalExtentEmpty: If the extent end is not later than its start.
        DimensionNotAvailable: If the specified dimension does not exist.
        ValueError: If the extent is not a two-element interval or both
            boundaries are ``None``.
    """
    if not isinstance(extent, (list, tuple)) or len(extent) != 2:
        raise ValueError(
            "The temporal extent must be an array with exactly two elements."
        )

    if extent[0] is None and extent[1] is None:
        raise ValueError(
            "The temporal extent must not have both boundaries set to null."
        )

    if dimension is not None and dimension.lower() not in ["t", "temporal", "time"]:
        raise DimensionNotAvailable(dimension)

    if not data:
        return data

    # Reuse the shared interval parsing/validation (raises TemporalExtentEmpty
    # when end <= start). filter_temporal only ever has a single interval.
    (start, end) = _parse_intervals([list(extent)])[0]

    matching_keys = [
        ts for ts in data.timestamps() if _timestamp_in_interval(ts, start, end)
    ]

    return data.filter_keys(matching_keys)
