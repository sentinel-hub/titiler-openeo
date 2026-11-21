"""titiler.openeo filter processes."""

import math
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Union

import numpy
from openeo_pg_parser_networkx.pg_schema import BoundingBox, TemporalInterval
from rasterio import windows
from rasterio.crs import CRS
from rasterio.transform import from_bounds
from rasterio.warp import transform_bounds

from ...errors import OpenEOException
from .data_model import ImageData, RasterStack
from .reduce import (
    DimensionNotAvailable,
    _interval_to_pair,
    _parse_intervals,
    _timestamp_in_interval,
)

__all__ = ["filter_bands", "filter_bbox", "filter_labels", "filter_temporal"]

TEMPORAL_DIMENSIONS = ("t", "temporal", "time")
SPECTRAL_DIMENSIONS = ("bands", "spectral")


def _extent_to_pair(extent: Any) -> List[Optional[str]]:
    """Normalize a temporal extent to a ``[start, end]`` list of RFC 3339 strings.

    The openEO graph parser passes ``extent`` as a ``TemporalInterval`` whose
    bounds are already parsed into pendulum datetimes, while direct Python/test
    callers pass a two-element list/tuple of strings (or ``None``). Both are
    reduced here (via the shared ``_interval_to_pair``) to the string-pair form
    understood by ``_parse_intervals``.
    """
    if isinstance(extent, (TemporalInterval, list, tuple)):
        return _interval_to_pair(extent)

    raise ValueError("The temporal extent must be an array with exactly two elements.")


def filter_temporal(
    data: RasterStack,
    extent: Union[TemporalInterval, List[Optional[str]]],
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
    pair = _extent_to_pair(extent)
    if len(pair) != 2:
        raise ValueError(
            "The temporal extent must be an array with exactly two elements."
        )

    if pair[0] is None and pair[1] is None:
        raise ValueError(
            "The temporal extent must not have both boundaries set to null."
        )

    if dimension is not None and dimension.lower() not in ["t", "temporal", "time"]:
        raise DimensionNotAvailable(dimension)

    if not data:
        return data

    # Reuse the shared interval parsing/validation (raises TemporalExtentEmpty
    # when end <= start). filter_temporal only ever has a single interval.
    (start, end) = _parse_intervals([pair])[0]

    matching_keys = [
        ts for ts in data.timestamps() if _timestamp_in_interval(ts, start, end)
    ]

    return data.filter_keys(matching_keys)


class BandFilterParameterMissing(OpenEOException):
    """Raised when filter_bands has no filter criterion."""

    def __init__(self) -> None:
        super().__init__(
            message="The process `filter_bands` requires any of the parameters "
            "`bands` or `wavelengths` to be set.",
            code="BandFilterParameterMissing",
            status_code=400,
        )


def _cube_band_names(data: RasterStack) -> List[str]:
    """Return the band names of the cube, from the stack or the first slice."""
    if data.band_names:
        return list(data.band_names)
    first = data.first
    return list(first.band_descriptions or [])


def _select_bands(img: ImageData, indexes: List[int], names: List[str]) -> ImageData:
    """Return a single ImageData with only the bands at ``indexes``."""
    return ImageData(
        img.array[indexes],
        assets=img.assets,
        crs=img.crs,
        bounds=img.bounds,
        cutline_mask=img.cutline_mask,
        metadata=img.metadata,
        band_names=names,
        band_descriptions=names,
    )


def filter_bands(
    data: RasterStack,
    bands: Optional[List[str]] = None,
    wavelengths: Optional[List[List[float]]] = None,
) -> RasterStack:
    """Filter the bands of the data cube by name.

    The order of ``bands`` defines the order of the bands in the result. The
    band names are the names of the cube bands (the names given to
    ``load_collection``). Filtering by wavelength is not supported, because
    the cube does not keep the wavelengths of its bands.

    Args:
        data: A data cube with bands.
        bands: A list of band names.
        wavelengths: Not supported.

    Returns:
        A data cube with only the selected bands, in the given order.

    Raises:
        BandFilterParameterMissing: If no criterion is given.
    """
    if wavelengths:
        raise OpenEOException(
            message="Filtering bands by wavelength is not supported by this "
            "back-end. Use band names.",
            code="FeatureUnsupported",
            status_code=400,
        )
    if not bands:
        raise BandFilterParameterMissing()
    if not data:
        return data

    current = _cube_band_names(data)
    missing = [b for b in bands if b not in current]
    if missing:
        raise OpenEOException(
            message=f"Band(s) {missing} do not exist in the data cube. "
            f"Available bands: {current}.",
            code="BandNotAvailable",
            status_code=400,
        )
    indexes = [current.index(b) for b in bands]
    names = list(bands)
    return data.map_images(
        lambda img: _select_bands(img, indexes, names), band_names=names
    )


def _crop_window(
    bounds: Any, width: int, height: int, extent: BoundingBox, crs: CRS
) -> windows.Window:
    """Return the pixel window of the pixels whose center is in the extent."""
    src_crs = CRS.from_user_input(extent.crs) if extent.crs else CRS.from_epsg(4326)
    west, south, east, north = transform_bounds(
        src_crs, crs, extent.west, extent.south, extent.east, extent.north
    )
    transform = from_bounds(*bounds, width, height)
    # Fractional pixel coordinates of the extent; a pixel is kept when its
    # center (col + 0.5, row + 0.5) is inside.
    col_w, row_n = ~transform * (west, north)
    col_e, row_s = ~transform * (east, south)
    col_off = max(math.ceil(min(col_w, col_e) - 0.5), 0)
    col_end = min(math.floor(max(col_w, col_e) - 0.5) + 1, width)
    row_off = max(math.ceil(min(row_n, row_s) - 0.5), 0)
    row_end = min(math.floor(max(row_n, row_s) - 0.5) + 1, height)
    if col_end <= col_off or row_end <= row_off:
        raise OpenEOException(
            message="The bounding box does not intersect the data cube.",
            code="NoDataAvailable",
            status_code=400,
        )
    return windows.Window(col_off, row_off, col_end - col_off, row_end - row_off)


def _crop(img: ImageData, window: windows.Window, bounds: Any) -> ImageData:
    """Return a single ImageData cropped to the pixel window."""
    rows, cols = window.toslices()
    cutline = img.cutline_mask
    return ImageData(
        img.array[:, rows, cols],
        assets=img.assets,
        crs=img.crs,
        bounds=bounds,
        cutline_mask=cutline[rows, cols] if cutline is not None else None,
        metadata=img.metadata,
        band_names=img.band_names,
        band_descriptions=img.band_descriptions,
    )


def filter_bbox(data: RasterStack, extent: BoundingBox) -> RasterStack:
    """Limit the data cube to the specified bounding box.

    A pixel is kept when its center is inside the bounding box. The bounding
    box is reprojected to the CRS of the data cube.

    Args:
        data: A raster data cube.
        extent: A bounding box. Its CRS defaults to EPSG:4326.

    Returns:
        A data cube restricted to the bounding box.
    """
    if not data:
        return data

    if data.width and data.height and data.bounds and data.dst_crs:
        bounds, width, height, crs = (
            data.bounds,
            data.width,
            data.height,
            data.dst_crs,
        )
    else:
        first = data.first
        bounds, width, height, crs = first.bounds, first.width, first.height, first.crs

    window = _crop_window(bounds, width, height, extent, crs)
    new_bounds = windows.bounds(window, from_bounds(*bounds, width, height))
    return data.map_images(
        lambda img: _crop(img, window, new_bounds),
        width=int(window.width),
        height=int(window.height),
        bounds=new_bounds,
    )


def _temporal_label(key: datetime) -> datetime:
    """Return the label of a temporal key (naive UTC) as an aware UTC datetime.

    The graph parser turns date strings of a process graph into aware
    datetimes, so an aware label compares with them (for example in ``gte``).
    """
    return key.replace(tzinfo=timezone.utc)


def filter_labels(
    data: RasterStack,
    condition: Callable,
    dimension: str,
    context: Optional[Any] = None,
) -> RasterStack:
    """Filter the labels of a dimension with a condition.

    The condition is evaluated ONCE, on the array of all labels (see the
    callback warning in apply.py), and must return one boolean per label.
    Temporal labels are UTC datetimes, so they compare with the dates of the
    process graph, for example with ``gte``.

    Args:
        data: A data cube.
        condition: A condition evaluated against each label (parameter
            ``value``). A label is kept if the condition returns true.
        dimension: The dimension to filter: ``t`` or ``bands``.
        context: Additional data passed to the condition.

    Returns:
        A data cube with only the labels that match the condition.

    Raises:
        DimensionNotAvailable: If the dimension does not exist.
    """
    dim = dimension.lower()
    if dim not in TEMPORAL_DIMENSIONS + SPECTRAL_DIMENSIONS:
        raise DimensionNotAvailable(dimension)
    if not data:
        return data

    keys = list(data.keys())
    # Only a band filter needs the band names (they can realize a slice).
    band_names = [] if dim in TEMPORAL_DIMENSIONS else _cube_band_names(data)
    labels: List[Any] = (
        [_temporal_label(k) for k in keys] if dim in TEMPORAL_DIMENSIONS else band_names
    )

    values = numpy.empty(len(labels), dtype=object)
    values[:] = labels
    named_parameters: Dict[str, Any] = {"value": values, "context": context}
    result = condition(
        values,
        positional_parameters={"value": 0},
        named_parameters=named_parameters,
    )
    keep = numpy.broadcast_to(numpy.asarray(result, dtype=bool), values.shape)

    if dim in TEMPORAL_DIMENSIONS:
        selected = [k for k, ok in zip(keys, keep) if ok]
        if not selected:
            raise OpenEOException(
                message="No temporal label matches the condition.",
                code="NoDataAvailable",
                status_code=400,
            )
        return data.filter_keys(selected)

    indexes = [i for i, ok in enumerate(keep) if ok]
    if not indexes:
        raise OpenEOException(
            message="No band label matches the condition.",
            code="NoDataAvailable",
            status_code=400,
        )
    names = [band_names[i] for i in indexes]
    return data.map_images(
        lambda img: _select_bands(img, indexes, names), band_names=names
    )
