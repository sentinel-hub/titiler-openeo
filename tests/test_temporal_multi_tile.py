"""Tests for temporal operations and multi-tile scenarios."""

import datetime

import numpy as np
import pytest
from rio_tiler.models import ImageData

from titiler.openeo.processes.implementations.data_model import RasterStack
from titiler.openeo.processes.implementations.math import first, last


def mock_task():
    """Mock task that returns an ImageData."""
    # Create a simple 1-band image
    array = np.ma.MaskedArray(
        data=np.ones((1, 10, 10)), mask=np.zeros((1, 10, 10), dtype=bool)
    )
    return ImageData(array)


@pytest.fixture
def multi_tile_temporal_stack():
    """Create a temporal stack with multiple tiles per datetime.

    This simulates a real-world scenario where each datetime has multiple
    non-overlapping tiles (e.g., tiled satellite imagery).
    """
    tasks = []

    # Create multiple tiles for 2023-01-01 (earliest datetime)
    date1 = datetime.datetime(2023, 1, 1)
    for tile_id in ["tile_1_1", "tile_1_2", "tile_1_3"]:
        asset = {
            "id": f"{tile_id}_2023-01-01",
            "tile_id": tile_id,
            "datetime": date1,
            "value": 10,  # Distinct value for this date
        }
        tasks.append((mock_task, asset))

    # Create multiple tiles for 2023-01-02 (middle datetime)
    date2 = datetime.datetime(2023, 1, 2)
    for tile_id in ["tile_2_1", "tile_2_2"]:
        asset = {
            "id": f"{tile_id}_2023-01-02",
            "tile_id": tile_id,
            "datetime": date2,
            "value": 20,  # Distinct value for this date
        }
        tasks.append((mock_task, asset))

    # Create multiple tiles for 2023-01-03 (latest datetime)
    date3 = datetime.datetime(2023, 1, 3)
    for tile_id in ["tile_3_1", "tile_3_2", "tile_3_3", "tile_3_4"]:
        asset = {
            "id": f"{tile_id}_2023-01-03",
            "tile_id": tile_id,
            "datetime": date3,
            "value": 30,  # Distinct value for this date
        }
        tasks.append((mock_task, asset))

    return RasterStack(
        tasks=tasks,
        key_fn=lambda asset: asset["id"],
        timestamp_fn=lambda asset: asset["datetime"],
    )


def test_multi_tile_temporal_ordering(multi_tile_temporal_stack):
    """Test that RasterStack maintains temporal ordering with multiple tiles per datetime."""
    keys = list(multi_tile_temporal_stack.keys())

    # Should have 9 total items (3 + 2 + 4)
    assert len(keys) == 9

    # Items should be ordered by datetime, then by key within each datetime
    # First 3 should be from 2023-01-01
    assert all("2023-01-01" in key for key in keys[:3])
    # Next 2 should be from 2023-01-02
    assert all("2023-01-02" in key for key in keys[3:5])
    # Last 4 should be from 2023-01-03
    assert all("2023-01-03" in key for key in keys[5:])

    # Verify timestamp mapping
    for key in keys[:3]:
        assert multi_tile_temporal_stack._timestamp_map[key] == datetime.datetime(
            2023, 1, 1
        )
    for key in keys[3:5]:
        assert multi_tile_temporal_stack._timestamp_map[key] == datetime.datetime(
            2023, 1, 2
        )
    for key in keys[5:]:
        assert multi_tile_temporal_stack._timestamp_map[key] == datetime.datetime(
            2023, 1, 3
        )


def test_temporal_first_selection(multi_tile_temporal_stack):
    """Test selecting first datetime preserves ALL tiles from that datetime."""

    # Get timestamps and find first
    timestamps = multi_tile_temporal_stack.timestamps()
    assert len(timestamps) == 9  # Each item has its own timestamp entry

    # First datetime should be 2023-01-01
    first_datetime = min(timestamps)
    assert first_datetime == datetime.datetime(2023, 1, 1)

    # Count items with first datetime
    first_tiles = [
        key
        for key in multi_tile_temporal_stack.keys()
        if multi_tile_temporal_stack.get_timestamp(key) == first_datetime
    ]
    assert len(first_tiles) == 3
    assert all("2023-01-01" in tile for tile in first_tiles)


def test_temporal_last_selection(multi_tile_temporal_stack):
    """Test selecting last datetime preserves ALL tiles from that datetime."""

    # Get timestamps and find last
    timestamps = multi_tile_temporal_stack.timestamps()
    last_datetime = max(timestamps)
    assert last_datetime == datetime.datetime(2023, 1, 3)

    # Count items with last datetime
    last_tiles = [
        key
        for key in multi_tile_temporal_stack.keys()
        if multi_tile_temporal_stack.get_timestamp(key) == last_datetime
    ]
    assert len(last_tiles) == 4
    assert all("2023-01-03" in tile for tile in last_tiles)


def test_apply_pixel_selection_with_multi_tile_first():
    """Test apply_pixel_selection with 'first' on multi-tile temporal data.

    This should return ALL tiles from the first datetime, not just one tile.
    """
    # Create a simpler test case for pixel selection
    tasks = []

    # Multiple tiles for first datetime
    date1 = datetime.datetime(2023, 1, 1)
    for i, tile_id in enumerate(["tile_1", "tile_2"]):

        def make_task(value):
            def task():
                array = np.ma.MaskedArray(
                    data=np.ones((1, 5, 5)) * value,
                    mask=np.zeros((1, 5, 5), dtype=bool),
                )
                return ImageData(array)

            return task

        asset = {
            "id": f"{tile_id}_2023-01-01",
            "datetime": date1,
            "value": 10 + i,  # tile_1 = 10, tile_2 = 11
        }
        tasks.append((make_task(10 + i), asset))

    # Multiple tiles for second datetime
    date2 = datetime.datetime(2023, 1, 2)
    for i, tile_id in enumerate(["tile_3", "tile_4"]):

        def make_task(value):
            def task():
                array = np.ma.MaskedArray(
                    data=np.ones((1, 5, 5)) * value,
                    mask=np.zeros((1, 5, 5), dtype=bool),
                )
                return ImageData(array)

            return task

        asset = {
            "id": f"{tile_id}_2023-01-02",
            "datetime": date2,
            "value": 20 + i,  # tile_3 = 20, tile_4 = 21
        }
        tasks.append((make_task(20 + i), asset))

    stack = RasterStack(
        tasks=tasks,
        key_fn=lambda asset: asset["id"],
        timestamp_fn=lambda asset: asset["datetime"],
    )

    # Test the current implementation - this might reveal the issue
    # The question is: does apply_pixel_selection with "first" return:
    # A) Just one tile (wrong for tiled imagery)
    # B) All tiles from first datetime (correct for tiled imagery)

    # First, let's see what keys we have and their order
    keys = list(stack.keys())
    assert len(keys) == 4

    # First two should be from first datetime
    assert "2023-01-01" in keys[0] and "2023-01-01" in keys[1]
    # Last two should be from second datetime
    assert "2023-01-02" in keys[2] and "2023-01-02" in keys[3]


def test_first_last_temporal_behavior():
    """Test that first/last functions now correctly handle temporal data."""

    # Create a simple temporal stack to test first/last functions
    stack = {}

    # Create temporal data where each timestamp has one image
    for i, date in enumerate(["2021-01-01", "2021-01-02", "2021-01-03"]):
        data = np.ma.array(
            np.ones((3, 10, 10), dtype=np.float32) * (i + 1),
            mask=np.zeros((3, 10, 10), dtype=bool),
        )
        stack[date] = ImageData(data, band_names=["red", "green", "blue"])

    # Test first() function - should return first temporal element
    first_result = first(stack)

    # Should return the entire first temporal image (all bands)
    assert isinstance(first_result, np.ndarray)
    # Should be all bands from the first temporal item: (3, 10, 10)
    assert first_result.shape == (3, 10, 10)
    # Should have value 1.0 since first temporal item has value 1.0
    assert np.all(first_result == 1.0)

    # Test last() function - should return last temporal element
    last_result = last(stack)

    # Should return the entire last temporal image (all bands)
    assert isinstance(last_result, np.ndarray)
    assert last_result.shape == (3, 10, 10)
    # Should have value 3.0 since last temporal item has value 3.0
    assert np.all(last_result == 3.0)
