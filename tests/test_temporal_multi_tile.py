"""Tests for temporal operations and multi-tile scenarios."""

import datetime

import numpy as np
import pytest
from rio_tiler.models import ImageData

from titiler.openeo.processes.implementations.data_model import LazyRasterStack
from titiler.openeo.processes.implementations.math import first


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

    return LazyRasterStack(
        tasks=tasks,
        key_fn=lambda asset: asset["id"],
        timestamp_fn=lambda asset: asset["datetime"],
    )


def test_multi_tile_temporal_ordering(multi_tile_temporal_stack):
    """Test that LazyRasterStack maintains temporal ordering with multiple tiles per datetime."""
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

    # Get the timestamp groups
    timestamp_groups = multi_tile_temporal_stack._timestamp_groups

    # Should have 3 datetime groups
    assert len(timestamp_groups) == 3

    # First datetime should have 3 tiles
    first_datetime = min(timestamp_groups.keys())
    first_tiles = timestamp_groups[first_datetime]
    assert len(first_tiles) == 3
    assert all("2023-01-01" in tile for tile in first_tiles)

    # Test that we can extract all first datetime tiles
    first_datetime_stack = {
        key: multi_tile_temporal_stack._tasks[i][1]
        for i, (_, asset) in enumerate(multi_tile_temporal_stack._tasks)
        for key in [multi_tile_temporal_stack._key_fn(asset)]
        if multi_tile_temporal_stack._timestamp_fn(asset) == first_datetime
    }

    assert len(first_datetime_stack) == 3


def test_temporal_last_selection(multi_tile_temporal_stack):
    """Test selecting last datetime preserves ALL tiles from that datetime."""

    # Get the timestamp groups
    timestamp_groups = multi_tile_temporal_stack._timestamp_groups

    # Last datetime should have 4 tiles
    last_datetime = max(timestamp_groups.keys())
    last_tiles = timestamp_groups[last_datetime]
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

    stack = LazyRasterStack(
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


def test_current_first_last_function_limitations():
    """Test to demonstrate current limitations of first/last functions."""

    # Create a simple temporal stack to test current first/last functions
    stack = {}

    # Create temporal data where each timestamp has one image
    for i, date in enumerate(["2021-01-01", "2021-01-02", "2021-01-03"]):
        data = np.ma.array(
            np.ones((3, 10, 10), dtype=np.float32) * (i + 1),
            mask=np.zeros((3, 10, 10), dtype=bool),
        )
        stack[date] = ImageData(data, band_names=["red", "green", "blue"])

    # Test current first() function
    first_result = first(stack)

    # Current implementation takes the first SPATIAL element (first row)
    # from each image, not the first TEMPORAL element
    assert isinstance(first_result, np.ndarray)
    assert first_result.shape[0] == len(stack)  # One result per image in stack

    # This is NOT what we want for temporal "first" - it should return
    # all tiles from the first timestamp, not the first spatial row from each image
