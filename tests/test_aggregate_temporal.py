"""Test aggregate_temporal process."""

from datetime import datetime, timezone

import numpy as np
import pytest
from rio_tiler.models import ImageData

from titiler.openeo.processes.implementations.data_model import RasterStack
from titiler.openeo.processes.implementations.reduce import (
    DimensionNotAvailable,
    DistinctDimensionLabelsRequired,
    TemporalExtentEmpty,
    _coerce_reduced_array,
    _normalize_to_naive_utc,
    _parse_temporal_value,
    aggregate_temporal,
)


def mean_reducer(data):
    """Reducer that computes mean across time."""
    arrays = []
    for key in data.keys():
        try:
            img = data[key]
            arrays.append(img.array)
        except KeyError:
            continue
    if not arrays:
        raise ValueError("No valid data found")
    stacked = np.ma.stack(arrays, axis=0)
    return np.ma.mean(stacked, axis=0)


def sum_reducer(data):
    """Reducer that computes sum across time."""
    arrays = []
    for key in data.keys():
        try:
            img = data[key]
            arrays.append(img.array)
        except KeyError:
            continue
    if not arrays:
        raise ValueError("No valid data found")
    stacked = np.ma.stack(arrays, axis=0)
    return np.ma.sum(stacked, axis=0)


def min_reducer(data):
    """Reducer that computes min across time."""
    arrays = []
    for key in data.keys():
        try:
            img = data[key]
            arrays.append(img.array)
        except KeyError:
            continue
    if not arrays:
        raise ValueError("No valid data found")
    stacked = np.ma.stack(arrays, axis=0)
    return np.ma.min(stacked, axis=0)


def max_reducer(data):
    """Reducer that computes max across time."""
    arrays = []
    for key in data.keys():
        try:
            img = data[key]
            arrays.append(img.array)
        except KeyError:
            continue
    if not arrays:
        raise ValueError("No valid data found")
    stacked = np.ma.stack(arrays, axis=0)
    return np.ma.max(stacked, axis=0)


def median_reducer(data):
    """Reducer that computes median across time."""
    arrays = []
    for key in data.keys():
        try:
            img = data[key]
            arrays.append(img.array)
        except KeyError:
            continue
    if not arrays:
        raise ValueError("No valid data found")
    stacked = np.ma.stack(arrays, axis=0)
    return np.ma.median(stacked, axis=0)


def _make_raster_stack(dates_values):
    """Helper: create a RasterStack from a list of (datetime, fill_value) tuples."""
    images = {}
    for dt, val in dates_values:
        array = np.ma.ones((2, 4, 4)) * val
        images[dt] = ImageData(
            array,
            assets=[f"asset_{dt.isoformat()}"],
            crs="EPSG:4326",
            bounds=(-180, -90, 180, 90),
            band_descriptions=["red", "green"],
        )
    return RasterStack.from_images(images)


def _make_lazy_raster_stack(dates_values, executed):
    """Helper: create a *lazy* RasterStack whose per-slice tasks record execution.

    Unlike ``_make_raster_stack`` (which uses ``from_images`` and pre-populates the
    cache), this builds genuine lazy tasks via the ``RasterStack`` constructor. Each
    task adds its datetime to the ``executed`` set when (and only when) it is run,
    so a test can assert exactly which time slices were actually fetched/decoded.
    """
    tasks = []
    for dt, val in dates_values:

        def make_task(dt=dt, val=val):
            def task():
                executed.add(dt)
                array = np.ma.ones((2, 4, 4)) * val
                return ImageData(
                    array,
                    assets=[f"asset_{dt.isoformat()}"],
                    crs="EPSG:4326",
                    bounds=(-180, -90, 180, 90),
                    band_descriptions=["red", "green"],
                )

            return task

        tasks.append((make_task(), {"datetime": dt}))

    return RasterStack(
        tasks=tasks,
        timestamp_fn=lambda asset: asset["datetime"],
        width=4,
        height=4,
        bounds=(-180, -90, 180, 90),
        dst_crs="EPSG:4326",
        band_names=["red", "green"],
    )


class TestAggregateTemporalBasic:
    """Basic aggregate_temporal tests."""

    def test_single_interval_single_image(self):
        """One interval containing one image."""
        data = _make_raster_stack([(datetime(2020, 6, 15), 5.0)])
        result = aggregate_temporal(
            data=data,
            intervals=[["2020-01-01", "2021-01-01"]],
            reducer=mean_reducer,
        )
        assert isinstance(result, RasterStack)
        assert len(result) == 1
        np.testing.assert_array_almost_equal(result.first.array.data, 5.0)

    def test_single_interval_multiple_images(self):
        """One interval containing multiple images - mean should average them."""
        data = _make_raster_stack(
            [
                (datetime(2020, 1, 15), 2.0),
                (datetime(2020, 6, 15), 4.0),
                (datetime(2020, 9, 15), 6.0),
            ]
        )
        result = aggregate_temporal(
            data=data,
            intervals=[["2020-01-01", "2021-01-01"]],
            reducer=mean_reducer,
        )
        assert len(result) == 1
        np.testing.assert_array_almost_equal(result.first.array.data, 4.0)

    def test_multiple_intervals(self):
        """Multiple non-overlapping intervals."""
        data = _make_raster_stack(
            [
                (datetime(2020, 3, 1), 10.0),
                (datetime(2020, 7, 1), 20.0),
                (datetime(2021, 3, 1), 30.0),
                (datetime(2021, 7, 1), 40.0),
            ]
        )
        result = aggregate_temporal(
            data=data,
            intervals=[
                ["2020-01-01", "2021-01-01"],
                ["2021-01-01", "2022-01-01"],
            ],
            reducer=mean_reducer,
        )
        assert len(result) == 2
        # Results keyed by interval start
        keys = sorted(result.keys())
        assert keys[0] == datetime(2020, 1, 1)
        assert keys[1] == datetime(2021, 1, 1)

        # 2020 interval: mean(10, 20) = 15
        np.testing.assert_array_almost_equal(result[keys[0]].array.data, 15.0)
        # 2021 interval: mean(30, 40) = 35
        np.testing.assert_array_almost_equal(result[keys[1]].array.data, 35.0)

    def test_yearly_intervals(self):
        """Yearly aggregation as in the spec example."""
        data = _make_raster_stack(
            [
                (datetime(2015, 6, 1), 1.0),
                (datetime(2016, 6, 1), 2.0),
                (datetime(2017, 6, 1), 3.0),
                (datetime(2018, 6, 1), 4.0),
                (datetime(2019, 6, 1), 5.0),
            ]
        )
        result = aggregate_temporal(
            data=data,
            intervals=[
                ["2015-01-01", "2016-01-01"],
                ["2016-01-01", "2017-01-01"],
                ["2017-01-01", "2018-01-01"],
                ["2018-01-01", "2019-01-01"],
                ["2019-01-01", "2020-01-01"],
            ],
            labels=["2015", "2016", "2017", "2018", "2019"],
            reducer=mean_reducer,
        )
        assert len(result) == 5


class TestAggregateTemporalIntervalsInput:
    """Graph path: intervals arrive as a TemporalIntervals (parsed datetimes).

    The openEO process-graph parser converts temporal-intervals into a
    ``TemporalIntervals`` whose bounds are pendulum datetimes, not strings.
    """

    def test_temporal_intervals_object(self):
        """A TemporalIntervals argument aggregates the same as a list of lists."""
        from openeo_pg_parser_networkx.pg_schema import TemporalIntervals

        data = _make_raster_stack(
            [
                (datetime(2021, 6, 15), 10.0),
                (datetime(2022, 6, 15), 20.0),
                (datetime(2023, 6, 15), 30.0),
                (datetime(2024, 1, 1), 99.0),  # outside all intervals
            ]
        )
        intervals = TemporalIntervals(
            [
                ["2023-06-01", "2023-07-01"],
                ["2022-06-01", "2022-07-01"],
                ["2021-06-01", "2021-07-01"],
            ]
        )
        result = aggregate_temporal(
            data=data, intervals=intervals, reducer=mean_reducer
        )
        assert len(result) == 3
        keys = sorted(result.keys())
        assert keys == [
            datetime(2021, 6, 1),
            datetime(2022, 6, 1),
            datetime(2023, 6, 1),
        ]
        np.testing.assert_array_almost_equal(result[keys[0]].array.data, 10.0)
        np.testing.assert_array_almost_equal(result[keys[1]].array.data, 20.0)
        np.testing.assert_array_almost_equal(result[keys[2]].array.data, 30.0)

    def test_temporal_intervals_passes_type_validation(self):
        """Regression: the @process validation layer must accept a TemporalIntervals.

        Previously ``intervals: List[List[Optional[str]]]`` made
        _validate_parameter_types reject the parser-supplied TemporalIntervals
        (DateTime elements), raising
        ``TypeError: expected 'List' but got 'TemporalIntervals'``.
        """
        from openeo_pg_parser_networkx.pg_schema import TemporalIntervals

        from titiler.openeo.processes.implementations.core import process

        data = _make_raster_stack(
            [
                (datetime(2021, 6, 15), 10.0),
                (datetime(2022, 6, 15), 20.0),
            ]
        )
        intervals = TemporalIntervals(
            [
                ["2021-06-01", "2021-07-01"],
                ["2022-06-01", "2022-07-01"],
            ]
        )
        wrapped = process(aggregate_temporal)
        result = wrapped(data=data, intervals=intervals, reducer=mean_reducer)
        assert len(result) == 2


class TestAggregateTemporalIntervalSemantics:
    """Test left-closed, right-open interval semantics."""

    def test_left_closed(self):
        """Image at exact interval start IS included."""
        data = _make_raster_stack([(datetime(2020, 1, 1), 10.0)])
        result = aggregate_temporal(
            data=data,
            intervals=[["2020-01-01", "2021-01-01"]],
            reducer=mean_reducer,
        )
        assert len(result) == 1
        np.testing.assert_array_almost_equal(result.first.array.data, 10.0)

    def test_right_open(self):
        """Image at exact interval end is NOT included."""
        data = _make_raster_stack(
            [
                (datetime(2020, 6, 1), 10.0),
                (
                    datetime(2021, 1, 1),
                    99.0,
                ),  # At boundary - should go to next interval
            ]
        )
        result = aggregate_temporal(
            data=data,
            intervals=[
                ["2020-01-01", "2021-01-01"],
                ["2021-01-01", "2022-01-01"],
            ],
            reducer=mean_reducer,
        )
        keys = sorted(result.keys())
        # 2020 interval should only have value 10
        np.testing.assert_array_almost_equal(result[keys[0]].array.data, 10.0)
        # 2021 interval should have value 99
        np.testing.assert_array_almost_equal(result[keys[1]].array.data, 99.0)

    def test_overlapping_intervals(self):
        """Overlapping intervals - image can appear in multiple intervals."""
        data = _make_raster_stack(
            [
                (datetime(2020, 6, 1), 10.0),
            ]
        )
        result = aggregate_temporal(
            data=data,
            intervals=[
                ["2020-01-01", "2021-01-01"],
                ["2020-06-01", "2021-06-01"],
            ],
            reducer=mean_reducer,
        )
        assert len(result) == 2
        keys = sorted(result.keys())
        # Image should be in both intervals
        np.testing.assert_array_almost_equal(result[keys[0]].array.data, 10.0)
        np.testing.assert_array_almost_equal(result[keys[1]].array.data, 10.0)

    def test_empty_interval_produces_nodata(self):
        """An interval with no matching data produces a no-data result."""
        data = _make_raster_stack(
            [
                (datetime(2020, 6, 1), 10.0),
            ]
        )
        result = aggregate_temporal(
            data=data,
            intervals=[
                ["2020-01-01", "2021-01-01"],
                ["2022-01-01", "2023-01-01"],  # No data in this range
            ],
            reducer=mean_reducer,
        )
        assert len(result) == 2
        keys = sorted(result.keys())
        # First interval has data
        np.testing.assert_array_almost_equal(result[keys[0]].array.data, 10.0)
        # Second interval should be all masked (no data)
        assert np.all(result[keys[1]].array.mask)

    def test_open_start_interval(self):
        """Interval with null start includes everything before end."""
        data = _make_raster_stack(
            [
                (datetime(2019, 1, 1), 5.0),
                (datetime(2020, 6, 1), 10.0),
            ]
        )
        result = aggregate_temporal(
            data=data,
            intervals=[[None, "2021-01-01"]],
            reducer=mean_reducer,
            labels=["all"],
        )
        assert len(result) == 1
        # mean(5, 10) = 7.5
        np.testing.assert_array_almost_equal(result.first.array.data, 7.5)

    def test_open_end_interval(self):
        """Interval with null end includes everything from start."""
        data = _make_raster_stack(
            [
                (datetime(2020, 1, 1), 5.0),
                (datetime(2025, 1, 1), 15.0),
            ]
        )
        result = aggregate_temporal(
            data=data,
            intervals=[["2020-01-01", None]],
            reducer=mean_reducer,
            labels=["all"],
        )
        assert len(result) == 1
        np.testing.assert_array_almost_equal(result.first.array.data, 10.0)

    def test_time_only_interval_daytime(self):
        """Time-only interval selects by time-of-day (non wrap-around)."""
        data = _make_raster_stack(
            [
                (datetime(2020, 1, 1, 5, 0, 0), 1.0),  # before window
                (datetime(2020, 1, 1, 9, 0, 0), 3.0),  # inside window
                (datetime(2020, 1, 1, 15, 0, 0), 5.0),  # inside window
                (datetime(2020, 1, 1, 21, 0, 0), 7.0),  # after window
            ]
        )
        result = aggregate_temporal(
            data=data,
            intervals=[["06:00:00", "18:00:00"]],
            reducer=mean_reducer,
            labels=["daytime"],
        )
        assert len(result) == 1
        # Only 3.0 and 5.0 should be included: mean = 4.0
        np.testing.assert_array_almost_equal(result.first.array.data, 4.0)

    def test_time_only_wrap_around_interval(self):
        """Wrap-around time-only interval splits midnight."""
        data = _make_raster_stack(
            [
                (
                    datetime(2020, 1, 1, 5, 0, 0),
                    1.0,
                ),  # inside wrap window (before 06:00)
                (datetime(2020, 1, 1, 9, 0, 0), 3.0),  # outside window
                (datetime(2020, 1, 1, 15, 0, 0), 5.0),  # outside window
                (
                    datetime(2020, 1, 1, 21, 0, 0),
                    7.0,
                ),  # inside wrap window (after 18:00)
            ]
        )
        result = aggregate_temporal(
            data=data,
            intervals=[["18:00:00", "06:00:00"]],
            reducer=mean_reducer,
            labels=["night"],
        )
        assert len(result) == 1
        # Only 1.0 (05:00) and 7.0 (21:00) should be included: mean = 4.0
        np.testing.assert_array_almost_equal(result.first.array.data, 4.0)


class TestAggregateTemporalReducers:
    """Test with different reducer functions."""

    def test_sum_reducer(self):
        """Test sum reducer."""
        data = _make_raster_stack(
            [
                (datetime(2020, 1, 1), 3.0),
                (datetime(2020, 6, 1), 7.0),
            ]
        )
        result = aggregate_temporal(
            data=data,
            intervals=[["2020-01-01", "2021-01-01"]],
            reducer=sum_reducer,
        )
        np.testing.assert_array_almost_equal(result.first.array.data, 10.0)

    def test_min_reducer(self):
        """Test min reducer."""
        data = _make_raster_stack(
            [
                (datetime(2020, 1, 1), 3.0),
                (datetime(2020, 6, 1), 7.0),
            ]
        )
        result = aggregate_temporal(
            data=data,
            intervals=[["2020-01-01", "2021-01-01"]],
            reducer=min_reducer,
        )
        np.testing.assert_array_almost_equal(result.first.array.data, 3.0)

    def test_max_reducer(self):
        """Test max reducer."""
        data = _make_raster_stack(
            [
                (datetime(2020, 1, 1), 3.0),
                (datetime(2020, 6, 1), 7.0),
            ]
        )
        result = aggregate_temporal(
            data=data,
            intervals=[["2020-01-01", "2021-01-01"]],
            reducer=max_reducer,
        )
        np.testing.assert_array_almost_equal(result.first.array.data, 7.0)

    def test_median_reducer(self):
        """Test median reducer."""
        data = _make_raster_stack(
            [
                (datetime(2020, 1, 1), 1.0),
                (datetime(2020, 6, 1), 3.0),
                (datetime(2020, 9, 1), 5.0),
            ]
        )
        result = aggregate_temporal(
            data=data,
            intervals=[["2020-01-01", "2021-01-01"]],
            reducer=median_reducer,
        )
        np.testing.assert_array_almost_equal(result.first.array.data, 3.0)


class TestAggregateTemporalLabels:
    """Test custom labels parameter."""

    def test_custom_string_labels(self):
        """Custom date-string labels override default interval-start keys."""
        data = _make_raster_stack(
            [
                (datetime(2020, 6, 1), 10.0),
                (datetime(2021, 6, 1), 20.0),
            ]
        )
        result = aggregate_temporal(
            data=data,
            intervals=[
                ["2020-01-01", "2021-01-01"],
                ["2021-01-01", "2022-01-01"],
            ],
            labels=["2020-07-01", "2021-07-01"],
            reducer=mean_reducer,
        )
        assert len(result) == 2
        keys = sorted(result.keys())
        assert keys[0] == datetime(2020, 7, 1)
        assert keys[1] == datetime(2021, 7, 1)

    def test_labels_count_mismatch_raises(self):
        """Number of labels must match number of intervals."""
        data = _make_raster_stack([(datetime(2020, 6, 1), 10.0)])
        with pytest.raises(ValueError, match="Number of labels"):
            aggregate_temporal(
                data=data,
                intervals=[
                    ["2020-01-01", "2021-01-01"],
                    ["2021-01-01", "2022-01-01"],
                ],
                labels=["only-one-label"],
                reducer=mean_reducer,
            )

    def test_duplicate_starts_without_labels_raises(self):
        """Duplicate interval starts without labels raises DistinctDimensionLabelsRequired."""
        data = _make_raster_stack([(datetime(2020, 6, 1), 10.0)])
        with pytest.raises(DistinctDimensionLabelsRequired):
            aggregate_temporal(
                data=data,
                intervals=[
                    ["2020-01-01", "2020-06-01"],
                    ["2020-01-01", "2020-12-01"],
                ],
                reducer=mean_reducer,
            )


class TestAggregateTemporalDimension:
    """Test dimension parameter handling."""

    def test_dimension_t(self):
        """dimension='t' works."""
        data = _make_raster_stack([(datetime(2020, 6, 1), 10.0)])
        result = aggregate_temporal(
            data=data,
            intervals=[["2020-01-01", "2021-01-01"]],
            reducer=mean_reducer,
            dimension="t",
        )
        assert len(result) == 1

    def test_dimension_temporal(self):
        """dimension='temporal' works."""
        data = _make_raster_stack([(datetime(2020, 6, 1), 10.0)])
        result = aggregate_temporal(
            data=data,
            intervals=[["2020-01-01", "2021-01-01"]],
            reducer=mean_reducer,
            dimension="temporal",
        )
        assert len(result) == 1

    def test_dimension_invalid_raises(self):
        """Invalid dimension name raises DimensionNotAvailable."""
        data = _make_raster_stack([(datetime(2020, 6, 1), 10.0)])
        with pytest.raises(DimensionNotAvailable):
            aggregate_temporal(
                data=data,
                intervals=[["2020-01-01", "2021-01-01"]],
                reducer=mean_reducer,
                dimension="bands",
            )


class TestAggregateTemporalErrors:
    """Test error handling."""

    def test_empty_data_raises(self):
        """Empty data cube raises ValueError."""
        with pytest.raises(ValueError):
            aggregate_temporal(
                data=RasterStack.from_images({}),
                intervals=[["2020-01-01", "2021-01-01"]],
                reducer=mean_reducer,
            )

    def test_empty_intervals_raises(self):
        """Empty intervals list raises ValueError."""
        data = _make_raster_stack([(datetime(2020, 6, 1), 10.0)])
        with pytest.raises(ValueError, match="At least one temporal interval"):
            aggregate_temporal(
                data=data,
                intervals=[],
                reducer=mean_reducer,
            )

    def test_invalid_interval_end_before_start(self):
        """End before start raises TemporalExtentEmpty."""
        data = _make_raster_stack([(datetime(2020, 6, 1), 10.0)])
        with pytest.raises(TemporalExtentEmpty):
            aggregate_temporal(
                data=data,
                intervals=[["2021-01-01", "2020-01-01"]],
                reducer=mean_reducer,
            )

    def test_interval_wrong_element_count(self):
        """Interval with wrong number of elements raises ValueError."""
        data = _make_raster_stack([(datetime(2020, 6, 1), 10.0)])
        with pytest.raises(ValueError, match="exactly two elements"):
            aggregate_temporal(
                data=data,
                intervals=[["2020-01-01"]],
                reducer=mean_reducer,
            )

    def test_no_matching_data_produces_nodata(self):
        """All intervals with no matching data produce fully-masked nodata images."""
        data = _make_raster_stack([(datetime(2020, 6, 1), 10.0)])
        # This should still return results (nodata images) since we have image refs
        result = aggregate_temporal(
            data=data,
            intervals=[["2025-01-01", "2026-01-01"]],
            reducer=mean_reducer,
        )
        # Should have one entry with all masked values
        assert len(result) == 1
        assert np.all(result.first.array.mask)


class TestAggregateTemporalPreservesMetadata:
    """Test that output preserves spatial metadata."""

    def test_preserves_crs_and_bounds(self):
        """Output images have same CRS and bounds as input."""
        data = _make_raster_stack(
            [
                (datetime(2020, 3, 1), 10.0),
                (datetime(2020, 9, 1), 20.0),
            ]
        )
        result = aggregate_temporal(
            data=data,
            intervals=[["2020-01-01", "2021-01-01"]],
            reducer=mean_reducer,
        )
        img = result.first
        assert str(img.crs) == "EPSG:4326"
        assert img.bounds == (-180, -90, 180, 90)

    def test_preserves_band_descriptions(self):
        """Output images have same band descriptions as input."""
        data = _make_raster_stack(
            [
                (datetime(2020, 3, 1), 10.0),
            ]
        )
        result = aggregate_temporal(
            data=data,
            intervals=[["2020-01-01", "2021-01-01"]],
            reducer=mean_reducer,
        )
        img = result.first
        assert img.band_descriptions == ["red", "green"]

    def test_preserves_spatial_shape(self):
        """Output images have same spatial dimensions as input."""
        data = _make_raster_stack(
            [
                (datetime(2020, 3, 1), 10.0),
                (datetime(2020, 9, 1), 20.0),
            ]
        )
        result = aggregate_temporal(
            data=data,
            intervals=[["2020-01-01", "2021-01-01"]],
            reducer=mean_reducer,
        )
        img = result.first
        assert img.array.shape == (2, 4, 4)


class TestAggregateTemporalNonContiguousIntervals:
    """Scenario: explicit, non-contiguous single-month intervals across years.

    The cube is loaded over a CONTINUOUS extent spanning several months across
    four years; most slices fall OUTSIDE every interval. These tests assert both
    correctness (only in-interval slices contribute) and performance (only
    in-interval slices are actually read).
    """

    # One month per year is requested, in reverse chronological order to also
    # exercise output ordering. Single-month, non-contiguous intervals.
    INTERVALS = [
        ["2021-07-01", "2021-08-01"],
        ["2020-07-01", "2020-08-01"],
        ["2019-07-01", "2019-08-01"],
    ]

    # Continuous monthly-ish coverage across 2019..2022.
    #   In-interval slices (July of 2019/2020/2021):
    IN_INTERVAL = [
        (datetime(2019, 7, 15), 10.0),  # -> 2019 bucket
        (datetime(2020, 7, 15), 20.0),  # -> 2020 bucket
        (datetime(2021, 7, 10), 30.0),  # -> 2021 bucket (mean with next)
        (datetime(2021, 7, 20), 40.0),  # -> 2021 bucket (mean -> 35)
    ]
    #   Out-of-interval slices that must NOT affect any bucket:
    OUT_OF_INTERVAL = [
        (datetime(2019, 1, 1), 444.0),
        (datetime(2019, 8, 15), 111.0),  # Aug 2019 (just after interval)
        (datetime(2020, 6, 15), 222.0),  # Jun 2020 (just before interval)
        (datetime(2021, 6, 30), 555.0),
        (datetime(2022, 7, 15), 333.0),  # 2022 has no interval at all
        (datetime(2022, 8, 1), 666.0),
    ]
    #   Boundary slice exactly on an interval END must be EXCLUDED ([start, end)):
    BOUNDARY = [
        (datetime(2021, 8, 1), 999.0),  # == end of the 2021 interval -> excluded
    ]

    def _all_slices(self):
        return self.IN_INTERVAL + self.OUT_OF_INTERVAL + self.BOUNDARY

    def test_only_in_interval_slices_contribute(self):
        """Exactly 3 buckets, each the mean of ONLY its in-interval slices."""
        data = _make_raster_stack(self._all_slices())
        result = aggregate_temporal(
            data=data,
            intervals=self.INTERVALS,
            reducer=mean_reducer,
        )

        # Exactly three output buckets (one per interval).
        assert len(result) == 3

        # Default labels = interval starts; output is temporally sorted regardless
        # of the (reverse-chronological) interval input order.
        keys = sorted(result.keys())
        assert keys == [
            datetime(2019, 7, 1),
            datetime(2020, 7, 1),
            datetime(2021, 7, 1),
        ]

        # Each bucket equals the mean of ONLY the in-interval slices.
        np.testing.assert_array_almost_equal(result[keys[0]].array.data, 10.0)
        np.testing.assert_array_almost_equal(result[keys[1]].array.data, 20.0)
        np.testing.assert_array_almost_equal(result[keys[2]].array.data, 35.0)

    def test_boundary_slice_on_interval_end_excluded(self):
        """A slice exactly on an interval end (2021-08-01) is excluded."""
        data = _make_raster_stack(self._all_slices())
        result = aggregate_temporal(
            data=data,
            intervals=self.INTERVALS,
            reducer=mean_reducer,
        )
        key_2021 = datetime(2021, 7, 1)
        # If 2021-08-01 (value 999) had leaked in, the mean would be far from 35.
        np.testing.assert_array_almost_equal(result[key_2021].array.data, 35.0)

    def test_out_of_interval_slices_are_not_read(self):
        """Performance: only in-interval slices are fetched/decoded.

        Uses a lazy RasterStack so each slice records when its task runs. After
        aggregate_temporal, the set of executed slices must equal exactly the
        in-interval slices -- proving out-of-interval (and boundary) slices are
        never materialized.
        """
        executed: set = set()
        data = _make_lazy_raster_stack(self._all_slices(), executed)

        # Sanity: nothing read yet just from constructing the stack / reading keys.
        assert executed == set()
        _ = data.timestamps()
        assert executed == set()

        result = aggregate_temporal(
            data=data,
            intervals=self.INTERVALS,
            reducer=mean_reducer,
        )

        # Only the four in-interval slices were ever realized.
        expected_read = {dt for dt, _ in self.IN_INTERVAL}
        assert executed == expected_read

        # And the boundary / out-of-interval slices were definitely not read.
        assert datetime(2021, 8, 1) not in executed  # boundary
        assert datetime(2022, 7, 15) not in executed  # year with no interval

        # Correctness still holds on the lazy path.
        keys = sorted(result.keys())
        np.testing.assert_array_almost_equal(result[keys[2]].array.data, 35.0)

    def test_in_interval_slices_are_read_concurrently(self):
        """The in-interval slices are pre-loaded in parallel, not serially.

        Each task sleeps briefly; if reads were serial the call would take at
        least N * delay. Concurrent prefetch brings it well under that bound.
        """
        import time as _time

        executed: set = set()
        delay = 0.2
        n_in_interval = len(self.IN_INTERVAL)

        def _make_slow_stack(dates_values):
            tasks = []
            for dt, val in dates_values:

                def make_task(dt=dt, val=val):
                    def task():
                        _time.sleep(delay)
                        executed.add(dt)
                        array = np.ma.ones((2, 4, 4)) * val
                        return ImageData(
                            array,
                            assets=[f"asset_{dt.isoformat()}"],
                            crs="EPSG:4326",
                            bounds=(-180, -90, 180, 90),
                            band_descriptions=["red", "green"],
                        )

                    return task

                tasks.append((make_task(), {"datetime": dt}))
            return RasterStack(
                tasks=tasks,
                timestamp_fn=lambda asset: asset["datetime"],
                max_workers=n_in_interval,
                width=4,
                height=4,
                bounds=(-180, -90, 180, 90),
                dst_crs="EPSG:4326",
                band_names=["red", "green"],
            )

        data = _make_slow_stack(self._all_slices())

        start = _time.monotonic()
        aggregate_temporal(
            data=data,
            intervals=self.INTERVALS,
            reducer=mean_reducer,
        )
        elapsed = _time.monotonic() - start

        # Only the in-interval slices were read.
        assert executed == {dt for dt, _ in self.IN_INTERVAL}
        # Concurrent: far below the serial lower bound (n * delay).
        assert elapsed < n_in_interval * delay


class TestParseTemporalValue:
    """Test _parse_temporal_value edge cases."""

    def test_tz_aware_datetime_normalized_to_utc(self):
        """Timezone-aware datetime strings are normalized to naive UTC."""
        result = _parse_temporal_value("2020-06-15T12:00:00+05:00")
        assert result.tzinfo is None
        assert result == datetime(2020, 6, 15, 7, 0, 0)

    def test_z_suffix(self):
        """Z suffix is handled as UTC."""
        result = _parse_temporal_value("2020-06-15T12:00:00Z")
        assert result.tzinfo is None
        assert result == datetime(2020, 6, 15, 12, 0, 0)


class TestNormalizeToNaiveUtc:
    """Test _normalize_to_naive_utc."""

    def test_aware_datetime_converted(self):
        """Tz-aware datetime is converted to naive UTC."""
        aware = datetime(2020, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
        result = _normalize_to_naive_utc(aware)
        assert result.tzinfo is None
        assert result == datetime(2020, 6, 15, 12, 0, 0)

    def test_naive_datetime_unchanged(self):
        """Naive datetime is returned as-is."""
        naive = datetime(2020, 6, 15, 12, 0, 0)
        result = _normalize_to_naive_utc(naive)
        assert result is naive


class TestCoerceReducedArray:
    """Test _coerce_reduced_array edge cases."""

    def test_dict_raises(self):
        """Dict input raises ValueError."""
        with pytest.raises(ValueError, match="not a dict"):
            _coerce_reduced_array({"key": "value"})

    def test_non_convertible_raises(self):
        """Non-convertible input raises ValueError."""

        class Unconvertible:
            """Object that cannot be converted to array."""

            def __array__(self, *args, **kwargs):
                raise TypeError("cannot convert")

        with pytest.raises(ValueError, match="array-like"):
            _coerce_reduced_array(Unconvertible())

    def test_list_converted(self):
        """List is converted to numpy array."""
        result = _coerce_reduced_array([1, 2, 3])
        assert isinstance(result, np.ndarray)


class TestAggregateTemporalCoverage:
    """Additional tests for coverage of edge cases."""

    def test_tz_aware_intervals(self):
        """Tz-aware interval bounds work with naive timestamps."""
        data = _make_raster_stack([(datetime(2020, 6, 15), 10.0)])
        result = aggregate_temporal(
            data=data,
            intervals=[["2020-01-01T00:00:00Z", "2021-01-01T00:00:00Z"]],
            reducer=mean_reducer,
        )
        assert len(result) == 1
        np.testing.assert_array_almost_equal(result.first.array.data, 10.0)

    def test_context_forwarded_to_reducer(self):
        """Context parameter is forwarded to the reducer."""
        data = _make_raster_stack([(datetime(2020, 6, 15), 10.0)])
        ctx = {"multiplier": 2}

        def context_reducer(data, context=None):
            """Reducer that uses context."""
            arrays = [data[k].array for k in data.keys()]
            stacked = np.ma.stack(arrays, axis=0)
            result = np.ma.mean(stacked, axis=0)
            if context and "multiplier" in context:
                result = result * context["multiplier"]
            return result

        result = aggregate_temporal(
            data=data,
            intervals=[["2020-01-01", "2021-01-01"]],
            reducer=context_reducer,
            context=ctx,
        )
        assert len(result) == 1
        np.testing.assert_array_almost_equal(result.first.array.data, 20.0)

    def test_numeric_labels(self):
        """Numeric labels produce synthetic datetime keys."""
        data = _make_raster_stack(
            [
                (datetime(2020, 6, 1), 10.0),
                (datetime(2021, 6, 1), 20.0),
            ]
        )
        result = aggregate_temporal(
            data=data,
            intervals=[
                ["2020-01-01", "2021-01-01"],
                ["2021-01-01", "2022-01-01"],
            ],
            labels=[1.0, 2.0],
            reducer=mean_reducer,
        )
        assert len(result) == 2

    def test_unsupported_label_type_raises(self):
        """Unsupported label type raises ValueError."""
        data = _make_raster_stack([(datetime(2020, 6, 1), 10.0)])
        with pytest.raises(ValueError, match="Unsupported label type"):
            aggregate_temporal(
                data=data,
                intervals=[["2020-01-01", "2021-01-01"]],
                labels=[None],
                reducer=mean_reducer,
            )

    def test_duplicate_label_keys_raises(self):
        """Labels that resolve to the same key raise DistinctDimensionLabelsRequired."""
        data = _make_raster_stack([(datetime(2020, 6, 1), 10.0)])
        with pytest.raises(DistinctDimensionLabelsRequired):
            aggregate_temporal(
                data=data,
                intervals=[
                    ["2020-01-01", "2020-06-01"],
                    ["2020-06-01", "2021-01-01"],
                ],
                labels=["2020-01-01", "2020-01-01"],
                reducer=mean_reducer,
            )

    def test_mixed_time_datetime_interval_raises(self):
        """Mixing time-only and datetime in one interval raises ValueError."""
        data = _make_raster_stack([(datetime(2020, 6, 1), 10.0)])
        with pytest.raises(ValueError, match="Cannot mix"):
            aggregate_temporal(
                data=data,
                intervals=[["06:00:00", "2021-01-01"]],
                reducer=mean_reducer,
                labels=["mixed"],
            )

    def test_reducer_returns_dict_raises(self):
        """Reducer returning a dict raises ValueError."""
        data = _make_raster_stack([(datetime(2020, 6, 1), 10.0)])

        def dict_reducer(data):
            """Bad reducer that returns a dict."""
            return {"bad": "result"}

        with pytest.raises(ValueError, match="not a dict"):
            aggregate_temporal(
                data=data,
                intervals=[["2020-01-01", "2021-01-01"]],
                reducer=dict_reducer,
            )

    def test_single_open_start_interval_without_labels(self):
        """A single open-start interval works without labels."""
        data = _make_raster_stack(
            [
                (datetime(2019, 1, 1), 5.0),
                (datetime(2020, 6, 1), 15.0),
            ]
        )
        result = aggregate_temporal(
            data=data,
            intervals=[[None, "2021-01-01"]],
            reducer=mean_reducer,
        )
        assert len(result) == 1
        np.testing.assert_array_almost_equal(result.first.array.data, 10.0)
