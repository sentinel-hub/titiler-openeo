"""Test filter_temporal process."""

from datetime import datetime

import numpy as np
import pytest
from rio_tiler.models import ImageData

from titiler.openeo.processes.implementations.data_model import RasterStack
from titiler.openeo.processes.implementations.filter import (
    DimensionNotAvailable,
    filter_temporal,
)
from titiler.openeo.processes.implementations.reduce import TemporalExtentEmpty


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


class TestFilterTemporalBasic:
    """Basic filter_temporal tests."""

    def test_keeps_only_matching_timestamps(self):
        """Only timestamps within [start, end) are kept."""
        data = _make_raster_stack(
            [
                (datetime(2020, 1, 15), 1.0),
                (datetime(2020, 6, 15), 2.0),
                (datetime(2021, 3, 1), 3.0),
            ]
        )
        result = filter_temporal(data, extent=["2020-01-01", "2021-01-01"])
        assert isinstance(result, RasterStack)
        assert sorted(result.keys()) == [
            datetime(2020, 1, 15),
            datetime(2020, 6, 15),
        ]

    def test_left_closed_right_open(self):
        """Start is included, end is excluded."""
        data = _make_raster_stack(
            [
                (datetime(2020, 1, 1), 1.0),
                (datetime(2021, 1, 1), 2.0),
            ]
        )
        result = filter_temporal(data, extent=["2020-01-01", "2021-01-01"])
        assert list(result.keys()) == [datetime(2020, 1, 1)]

    def test_open_start(self):
        """A null lower boundary keeps everything before the end."""
        data = _make_raster_stack(
            [
                (datetime(2019, 1, 1), 1.0),
                (datetime(2020, 6, 15), 2.0),
                (datetime(2021, 3, 1), 3.0),
            ]
        )
        result = filter_temporal(data, extent=[None, "2021-01-01"])
        assert sorted(result.keys()) == [
            datetime(2019, 1, 1),
            datetime(2020, 6, 15),
        ]

    def test_open_end(self):
        """A null upper boundary keeps everything from the start onwards."""
        data = _make_raster_stack(
            [
                (datetime(2019, 1, 1), 1.0),
                (datetime(2020, 6, 15), 2.0),
                (datetime(2021, 3, 1), 3.0),
            ]
        )
        result = filter_temporal(data, extent=["2020-01-01", None])
        assert sorted(result.keys()) == [
            datetime(2020, 6, 15),
            datetime(2021, 3, 1),
        ]

    def test_datetime_extent_with_timezone(self):
        """RFC 3339 date-time strings with Z suffix are supported."""
        data = _make_raster_stack(
            [
                (datetime(2020, 1, 15), 1.0),
                (datetime(2020, 6, 15), 2.0),
            ]
        )
        result = filter_temporal(
            data,
            extent=["2020-01-01T00:00:00Z", "2020-02-01T00:00:00Z"],
        )
        assert list(result.keys()) == [datetime(2020, 1, 15)]

    def test_empty_result(self):
        """An extent matching nothing yields an empty stack."""
        data = _make_raster_stack([(datetime(2020, 6, 15), 1.0)])
        result = filter_temporal(data, extent=["2010-01-01", "2011-01-01"])
        assert len(result) == 0

    def test_preserves_data_values(self):
        """Filtered images preserve their pixel values and metadata."""
        data = _make_raster_stack(
            [
                (datetime(2020, 6, 15), 7.0),
                (datetime(2021, 6, 15), 9.0),
            ]
        )
        result = filter_temporal(data, extent=["2020-01-01", "2021-01-01"])
        img = result[datetime(2020, 6, 15)]
        np.testing.assert_array_almost_equal(img.array.data, 7.0)
        assert img.band_descriptions == ["red", "green"]


class TestFilterTemporalDimension:
    """Tests for the optional dimension parameter."""

    @pytest.mark.parametrize("dimension", ["t", "temporal", "time", "T", "Temporal"])
    def test_valid_dimension_names(self, dimension):
        """Recognized temporal dimension names are accepted."""
        data = _make_raster_stack([(datetime(2020, 6, 15), 1.0)])
        result = filter_temporal(
            data, extent=["2020-01-01", "2021-01-01"], dimension=dimension
        )
        assert len(result) == 1

    def test_unknown_dimension_raises(self):
        """An unknown dimension raises DimensionNotAvailable."""
        data = _make_raster_stack([(datetime(2020, 6, 15), 1.0)])
        with pytest.raises(DimensionNotAvailable):
            filter_temporal(
                data, extent=["2020-01-01", "2021-01-01"], dimension="bands"
            )


class TestFilterTemporalValidation:
    """Validation/exception tests."""

    def test_empty_extent_raises(self):
        """end <= start raises TemporalExtentEmpty."""
        data = _make_raster_stack([(datetime(2020, 6, 15), 1.0)])
        with pytest.raises(TemporalExtentEmpty):
            filter_temporal(data, extent=["2021-01-01", "2020-01-01"])

    def test_both_boundaries_null_raises(self):
        """Both boundaries null is invalid."""
        data = _make_raster_stack([(datetime(2020, 6, 15), 1.0)])
        with pytest.raises(ValueError):
            filter_temporal(data, extent=[None, None])

    def test_wrong_extent_length_raises(self):
        """An extent that is not a two-element array is invalid."""
        data = _make_raster_stack([(datetime(2020, 6, 15), 1.0)])
        with pytest.raises(ValueError):
            filter_temporal(data, extent=["2020-01-01"])

    def test_empty_data_returned_as_is(self):
        """Filtering an empty stack returns it unchanged."""
        data = _make_raster_stack([(datetime(2020, 6, 15), 1.0)])
        empty = data.filter_keys([])
        result = filter_temporal(empty, extent=["2020-01-01", "2021-01-01"])
        assert len(result) == 0


class TestFilterKeys:
    """Tests for the RasterStack.filter_keys helper."""

    def test_subset_preserves_order_and_metadata(self):
        """Subset keeps temporal order and spatial metadata."""
        data = _make_raster_stack(
            [
                (datetime(2020, 1, 1), 1.0),
                (datetime(2020, 2, 1), 2.0),
                (datetime(2020, 3, 1), 3.0),
            ]
        )
        subset = data.filter_keys([datetime(2020, 3, 1), datetime(2020, 1, 1)])
        assert list(subset.keys()) == [datetime(2020, 1, 1), datetime(2020, 3, 1)]
        assert subset.band_names == ["red", "green"]
        assert subset.bounds == (-180, -90, 180, 90)

    def test_unknown_keys_ignored(self):
        """Keys not present in the stack are ignored."""
        data = _make_raster_stack([(datetime(2020, 1, 1), 1.0)])
        subset = data.filter_keys([datetime(1999, 1, 1)])
        assert len(subset) == 0

    def test_preserves_geometry_cutline_for_cached_items(self):
        """Cached items keep their footprint geometry so cutline_mask is computed.

        Regression test: replacing refs with ImageRef.from_image would drop the
        asset geometry and make cutline_mask() return None. The subset must keep
        the geometry-aware ref while avoiding task re-execution.
        """
        geometry = {
            "type": "Polygon",
            "coordinates": [[[-10, -10], [10, -10], [10, 10], [-10, 10], [-10, -10]]],
        }
        calls = {"n": 0}

        def make_task():
            def task():
                calls["n"] += 1
                arr = np.ma.ones((2, 8, 8))
                return ImageData(
                    arr,
                    crs="EPSG:4326",
                    bounds=(-180, -90, 180, 90),
                    band_descriptions=["red", "green"],
                )

            return task

        key = datetime(2020, 6, 15)
        tasks = [(make_task(), {"datetime": key, "geometry": geometry})]
        stack = RasterStack(
            tasks=tasks,
            timestamp_fn=lambda asset: asset["datetime"],
            width=8,
            height=8,
            bounds=(-180, -90, 180, 90),
            dst_crs="EPSG:4326",
            band_names=["red", "green"],
        )

        # Realize so the data is cached, then subset.
        _ = stack[key]
        assert calls["n"] == 1

        subset = stack.filter_keys([key])
        ref = subset.get_image_ref(key)
        assert ref is not None

        # Geometry-based cutline mask is still available (not None) ...
        cutline = ref.cutline_mask()
        assert cutline is not None
        assert cutline.shape == (8, 8)
        # ... and computing it did NOT re-execute the underlying task.
        assert calls["n"] == 1

        # Realizing returns the cached image without re-running the task.
        assert subset[key] is not None
        assert calls["n"] == 1
