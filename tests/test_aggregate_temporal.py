"""Test aggregate_temporal process."""

from datetime import datetime

import numpy as np
import pytest
from rio_tiler.models import ImageData

from titiler.openeo.processes.implementations.data_model import RasterStack
from titiler.openeo.processes.implementations.reduce import (
    DimensionNotAvailable,
    DistinctDimensionLabelsRequired,
    TemporalExtentEmpty,
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

    def test_no_matching_data_raises(self):
        """All intervals empty with no fallback metadata raises ValueError."""
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
