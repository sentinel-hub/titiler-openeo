"""Tests for not, sum, array_interpolate_linear, aggregate_temporal_period
and the filter_* processes (#441)."""

from datetime import datetime

import numpy as np
import pytest
from openeo_pg_parser_networkx.graph import OpenEOProcessGraph
from openeo_pg_parser_networkx.pg_schema import BoundingBox
from rasterio.crs import CRS
from rio_tiler.models import ImageData

from titiler.openeo.errors import OpenEOException
from titiler.openeo.processes import process_registry
from titiler.openeo.processes.implementations.arrays import array_interpolate_linear
from titiler.openeo.processes.implementations.data_model import RasterStack
from titiler.openeo.processes.implementations.filter import (
    BandFilterParameterMissing,
    filter_bands,
    filter_bbox,
    filter_labels,
)
from titiler.openeo.processes.implementations.logic import not_
from titiler.openeo.processes.implementations.math import sum as sum_
from titiler.openeo.processes.implementations.reduce import (
    DimensionNotAvailable,
    _period_intervals,
    aggregate_temporal_period,
)


def _run(pg: dict, **named_parameters):
    """Compile and execute a process graph against the real registry."""
    callable_ = OpenEOProcessGraph(pg_data={"process_graph": pg}).to_callable(
        process_registry=process_registry
    )
    return callable_(named_parameters=named_parameters)


def _daily_stack(values, start_day: int = 1, bands=("B1",)) -> RasterStack:
    """A stack with one 2x2 image per day of June 2024, one value per day."""
    return RasterStack.from_images(
        {
            datetime(2024, 6, start_day + i): ImageData(
                np.ma.MaskedArray(np.full((len(bands), 2, 2), v, np.float32)),
                band_descriptions=list(bands),
            )
            for i, v in enumerate(values)
        }
    )


# --- not -------------------------------------------------------------------


@pytest.mark.parametrize("x,expected", [(None, None), (False, True), (True, False)])
def test_not_spec_examples(x, expected):
    assert not_(x) is expected


def test_not_keeps_mask():
    x = np.ma.MaskedArray([True, False, True], mask=[False, False, True])
    result = not_(x)
    assert result[:2].tolist() == [False, True]
    assert result.mask.tolist() == [False, False, True]


# --- sum -------------------------------------------------------------------


@pytest.mark.parametrize(
    "data,ignore_nodata,expected",
    [
        ([5, 1], True, 6),
        ([-2, 4, 2.5], True, 4.5),
        ([1, None], False, None),
        ([1, None], True, 1),
        ([100], True, 100),
        ([None], False, None),
        ([], True, None),
    ],
)
def test_sum_spec_examples(data, ignore_nodata, expected):
    assert sum_(data, ignore_nodata=ignore_nodata) == expected


def test_sum_reduces_leading_axis_with_nodata():
    data = np.ma.MaskedArray(
        [[1, 1], [2, 5], [3, 1]], mask=[[False, True], [False, True], [False, True]]
    )
    result = sum_(data)
    assert result[0] == 6
    assert result.mask.tolist() == [False, True]
    data.mask[1, 0] = True
    assert sum_(data)[0] == 4
    assert sum_(data, ignore_nodata=False).mask[0]


def test_sum_rasterstack_counts_snow_days():
    stack = _daily_stack([1, 0, 1, 1])
    np.testing.assert_array_equal(sum_(stack), np.full((1, 2, 2), 3))


# --- array_interpolate_linear ------------------------------------------------


# Official openEO test cases (openeo-processes tests/array_interpolate_linear.json5).
@pytest.mark.parametrize(
    "data,expected",
    [
        ([None, 1, None, 6, None, -8], [None, 1, 3.5, 6, -1, -8]),
        ([1, np.nan, 6, np.nan, -8, np.nan], [1, 3.5, 6, -1, -8, np.nan]),
        (
            [-np.inf, np.nan, 0.1, np.nan, 0.2, np.nan, np.inf],
            [-np.inf, -np.inf, 0.1, 0.15, 0.2, np.inf, np.inf],
        ),
        ([None, 1, None, None], [None, 1, None, None]),
        ([None, None, None], [None, None, None]),
        ({"a": 1.0, "b": np.nan, "c": 2.0}, {"a": 1.0, "b": 1.5, "c": 2.0}),
        ({1: 1.0, 3: None, 4: 4.0}, {1: 1.0, 3: 3.0, 4: 4.0}),
    ],
)
def test_array_interpolate_linear_official_cases(data, expected):
    result = array_interpolate_linear(data)
    if isinstance(expected, dict):
        assert list(result) == list(expected)
        result, expected = list(result.values()), list(expected.values())
    assert len(result) == len(expected)
    for got, want in zip(result, expected):
        if want is None:
            assert got is None
        elif np.isnan(want):
            assert np.isnan(got)
        else:
            assert got == pytest.approx(want)


def test_array_interpolate_linear_per_pixel_along_time():
    """Gaps (masked or NaN) are filled per pixel; edges stay as they are."""
    data = np.ma.MaskedArray(
        np.array([[0.0, 0.0], [np.nan, 9.0], [0.0, 0.0], [4.0, 3.0]], dtype=np.float32)[
            :, np.newaxis, :
        ],
        mask=np.array([[True, False], [False, False], [True, True], [False, False]])[
            :, np.newaxis, :
        ],
    )
    result = array_interpolate_linear(data)
    assert result.dtype == np.float32
    # Pixel 0: leading masked value stays masked; the NaN at t=1 has no valid
    # value before it, so it stays NaN.
    assert result.mask[0, 0, 0]
    assert np.isnan(result.data[1, 0, 0])
    # Pixel 1: t=2 is interpolated between 9 (t=1) and 3 (t=3).
    assert result[2, 0, 1] == pytest.approx(6.0)
    assert not result.mask[2, 0, 1]


def test_apply_dimension_array_interpolate_linear_via_graph():
    """Gap filling along t uses the dates as weights, not the indices.

    Dates on June 1, 2, 4 and 8: the gaps on June 2 and 4 are 1/7 and 3/7 of
    the way from 1 to 8 (index weights would give 1/3 and 2/3).
    """
    stack = RasterStack.from_images(
        {
            datetime(2024, 6, day): ImageData(
                np.ma.MaskedArray(np.full((1, 2, 2), v, np.float32)),
                band_descriptions=["B1"],
            )
            for day, v in [(1, 1), (2, 0), (4, 0), (8, 8)]
        }
    )
    for key in list(stack.keys())[1:3]:
        stack[key].array.mask = True
    pg = {
        "apply": {
            "process_id": "apply_dimension",
            "arguments": {
                "data": {"from_parameter": "data"},
                "dimension": "t",
                "process": {
                    "process_graph": {
                        "interp": {
                            "process_id": "array_interpolate_linear",
                            "arguments": {"data": {"from_parameter": "data"}},
                            "result": True,
                        }
                    }
                },
            },
            "result": True,
        }
    }
    result = _run(pg, data=stack)
    values = [float(img.array[0, 0, 0]) for img in result.values()]
    assert values == pytest.approx([1.0, 2.0, 4.0, 8.0])


# --- aggregate_temporal_period -----------------------------------------------


@pytest.mark.parametrize(
    "period,timestamps,expected",
    [
        (
            "dekad",
            [datetime(2024, 1, 5), datetime(2024, 2, 25)],
            ["2024-01-01", "2024-01-11", "2024-01-21", "2024-02-01", "2024-02-11"]
            + ["2024-02-21"],
        ),
        (
            "season",
            [datetime(2024, 1, 15), datetime(2024, 6, 1)],
            ["2023-12-01", "2024-03-01", "2024-06-01"],
        ),
        (
            "tropical-season",
            [datetime(2024, 2, 1), datetime(2024, 6, 1)],
            ["2023-11-01", "2024-05-01"],
        ),
        (
            "week",
            [datetime(2024, 6, 5), datetime(2024, 6, 12)],
            ["2024-06-03", "2024-06-10"],
        ),
        ("decade", [datetime(2020, 6, 1)], ["2020-01-01"]),
        ("decade-ad", [datetime(2020, 6, 1)], ["2011-01-01"]),
    ],
)
def test_period_intervals(period, timestamps, expected):
    intervals = _period_intervals(timestamps, period)
    assert [start[:10] for start, _ in intervals] == expected
    # Contiguous: each interval ends where the next starts.
    for (_, end), (start, _) in zip(intervals, intervals[1:]):
        assert end == start


def test_period_intervals_dekad_ends_on_next_month():
    intervals = _period_intervals([datetime(2024, 2, 25)], "dekad")
    assert intervals == [["2024-02-21T00:00:00", "2024-03-01T00:00:00"]]


def test_aggregate_temporal_period_month_with_sum_via_graph():
    """Monthly sum through the real executor: each month gets its own result."""
    stack = RasterStack.from_images(
        {
            datetime(2024, 5, 30): ImageData(np.ma.MaskedArray(np.ones((1, 2, 2)))),
            datetime(2024, 5, 31): ImageData(np.ma.MaskedArray(np.ones((1, 2, 2)))),
            datetime(2024, 6, 1): ImageData(np.ma.MaskedArray(np.ones((1, 2, 2)))),
        }
    )
    pg = {
        "agg": {
            "process_id": "aggregate_temporal_period",
            "arguments": {
                "data": {"from_parameter": "data"},
                "period": "month",
                "reducer": {
                    "process_graph": {
                        "sum": {
                            "process_id": "sum",
                            "arguments": {"data": {"from_parameter": "data"}},
                            "result": True,
                        }
                    }
                },
            },
            "result": True,
        }
    }
    result = _run(pg, data=stack)
    assert list(result.keys()) == [datetime(2024, 5, 1), datetime(2024, 6, 1)]
    assert [float(img.array[0, 0, 0]) for img in result.values()] == [2.0, 1.0]


def test_aggregate_temporal_period_bad_dimension():
    with pytest.raises(DimensionNotAvailable):
        aggregate_temporal_period(_daily_stack([1]), "month", sum_, dimension="x")


# --- filter_bands ------------------------------------------------------------


def test_filter_bands_selects_and_orders():
    stack = RasterStack.from_images(
        {
            datetime(2024, 6, 1): ImageData(
                np.ma.MaskedArray(np.stack([np.full((2, 2), v) for v in (1, 2, 3)])),
                band_descriptions=["B02", "B03", "B04"],
            )
        }
    )
    result = filter_bands(stack, bands=["B04", "B02"])
    img = next(iter(result.values()))
    assert img.band_descriptions == ["B04", "B02"]
    assert img.array[:, 0, 0].tolist() == [3, 1]
    assert result.band_names == ["B04", "B02"]


def test_filter_bands_errors():
    stack = _daily_stack([1])
    with pytest.raises(BandFilterParameterMissing):
        filter_bands(stack)
    with pytest.raises(OpenEOException, match="do not exist"):
        filter_bands(stack, bands=["B99"])
    with pytest.raises(OpenEOException, match="wavelength"):
        filter_bands(stack, wavelengths=[[0.4, 0.5]])


# --- filter_bbox -------------------------------------------------------------


def test_filter_bbox_keeps_pixels_with_center_inside():
    utm = CRS.from_epsg(32631)
    data = np.arange(100, dtype=np.float32).reshape(1, 10, 10)
    stack = RasterStack.from_images(
        {
            datetime(2024, 6, 1): ImageData(
                np.ma.MaskedArray(data), crs=utm, bounds=(0, 0, 100, 100)
            )
        }
    )
    # Columns with center in [25, 61]: 25, 35, 45, 55 -> columns 2..5.
    # Rows with center in [12, 48] (north 48 -> row 5 center 45 ... ): 45, 35, 25, 15.
    extent = BoundingBox(west=21, south=12, east=61, north=48, crs="EPSG:32631")
    result = filter_bbox(stack, extent)
    img = next(iter(result.values()))
    assert img.array.shape == (1, 4, 4)
    assert img.bounds == (20.0, 10.0, 60.0, 50.0)
    assert img.array[0, 0, 0] == data[0, 5, 2]
    assert (result.width, result.height) == (4, 4)


def test_filter_bbox_reprojects_extent():
    stack = RasterStack.from_images(
        {
            datetime(2024, 6, 1): ImageData(
                np.ma.MaskedArray(np.ones((1, 10, 10), np.float32)),
                crs=CRS.from_epsg(4326),
                bounds=(0, 0, 10, 10),
            )
        }
    )
    extent = BoundingBox(west=2, south=2, east=5, north=5)
    img = next(iter(filter_bbox(stack, extent).values()))
    assert img.bounds == (2.0, 2.0, 5.0, 5.0)


def test_filter_bbox_outside_raises():
    stack = RasterStack.from_images(
        {
            datetime(2024, 6, 1): ImageData(
                np.ma.MaskedArray(np.ones((1, 10, 10), np.float32)),
                crs=CRS.from_epsg(4326),
                bounds=(0, 0, 10, 10),
            )
        }
    )
    with pytest.raises(OpenEOException, match="does not intersect"):
        filter_bbox(stack, BoundingBox(west=20, south=20, east=30, north=30))


# --- filter_labels -----------------------------------------------------------


def test_filter_labels_temporal_via_graph():
    """The condition sees ISO 8601 labels and is evaluated once on all labels."""
    stack = _daily_stack([1, 2, 3, 4])
    pg = {
        "filter": {
            "process_id": "filter_labels",
            "arguments": {
                "data": {"from_parameter": "data"},
                "dimension": "t",
                "condition": {
                    "process_graph": {
                        "gte": {
                            "process_id": "gte",
                            "arguments": {
                                "x": {"from_parameter": "value"},
                                "y": "2024-06-03",
                            },
                            "result": True,
                        }
                    }
                },
            },
            "result": True,
        }
    }
    result = _run(pg, data=stack)
    assert list(result.keys()) == [datetime(2024, 6, 3), datetime(2024, 6, 4)]


def test_filter_labels_bands_via_graph():
    stack = _daily_stack([1], bands=("B02", "B03", "B04"))
    pg = {
        "filter": {
            "process_id": "filter_labels",
            "arguments": {
                "data": {"from_parameter": "data"},
                "dimension": "bands",
                "condition": {
                    "process_graph": {
                        "neq": {
                            "process_id": "neq",
                            "arguments": {
                                "x": {"from_parameter": "value"},
                                "y": "B03",
                            },
                            "result": True,
                        }
                    }
                },
            },
            "result": True,
        }
    }
    result = _run(pg, data=stack)
    assert next(iter(result.values())).band_descriptions == ["B02", "B04"]


def test_filter_labels_bad_dimension():
    with pytest.raises(DimensionNotAvailable):
        filter_labels(_daily_stack([1]), lambda *a, **k: True, "platform")


def test_new_processes_registered():
    for name in (
        "not",
        "sum",
        "array_interpolate_linear",
        "aggregate_temporal_period",
        "filter_bands",
        "filter_bbox",
        "filter_labels",
        "slope",
        "aspect",
        "apply_kernel",
    ):
        assert process_registry[name].spec["id"] == name
