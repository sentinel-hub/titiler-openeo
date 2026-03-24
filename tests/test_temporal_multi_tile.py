"""Tests for temporal operations with RasterStack.

Note: With the simplified RasterStack design using datetime keys directly,
each timestamp is unique. Multiple items per timestamp is no longer supported.
"""

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
def temporal_stack():
    """Create a temporal stack with unique timestamps.

    Each item has a unique datetime, representing a typical time series.
    """
    tasks = []

    # Create items with unique timestamps across several days
    base_date = datetime.datetime(2023, 1, 1)
    for i in range(9):
        dt = base_date + datetime.timedelta(hours=i * 8)  # Every 8 hours
        asset = {
            "datetime": dt,
            "value": 10 + i,  # Distinct value for each item
        }
        tasks.append((mock_task, asset))

    return RasterStack(
        tasks=tasks,
        timestamp_fn=lambda asset: asset["datetime"],
    )


def test_temporal_ordering(temporal_stack):
    """Test that RasterStack maintains temporal ordering."""
    keys = list(temporal_stack.keys())

    # Should have 9 total items
    assert len(keys) == 9

    # Keys should be datetime objects in sorted order
    assert all(isinstance(key, datetime.datetime) for key in keys)

    # Verify ordering - each key should be greater than the previous
    for i in range(1, len(keys)):
        assert keys[i] > keys[i - 1], f"Keys not in order: {keys[i-1]} >= {keys[i]}"

    # First key should be 2023-01-01 00:00:00
    assert keys[0] == datetime.datetime(2023, 1, 1, 0, 0, 0)

    # Last key should be 2023-01-03 16:00:00 (8 intervals of 8 hours from start)
    assert keys[-1] == datetime.datetime(2023, 1, 3, 16, 0, 0)


def test_temporal_first_selection(temporal_stack):
    """Test selecting first datetime."""

    # Get timestamps (same as keys now)
    timestamps = temporal_stack.timestamps()
    assert len(timestamps) == 9

    # First datetime should be 2023-01-01 00:00:00
    first_datetime = min(timestamps)
    assert first_datetime == datetime.datetime(2023, 1, 1, 0, 0, 0)

    # Since keys ARE timestamps, first key is first timestamp
    keys = list(temporal_stack.keys())
    assert keys[0] == first_datetime


def test_temporal_last_selection(temporal_stack):
    """Test selecting last datetime."""

    # Get timestamps (same as keys now)
    timestamps = temporal_stack.timestamps()
    last_datetime = max(timestamps)
    assert last_datetime == datetime.datetime(2023, 1, 3, 16, 0, 0)

    # Since keys ARE timestamps, last key is last timestamp
    keys = list(temporal_stack.keys())
    assert keys[-1] == last_datetime


def test_apply_pixel_selection_with_temporal_first():
    """Test apply_pixel_selection with 'first' on temporal data.

    With datetime keys, 'first' returns the item with the earliest timestamp.
    """
    # Create a simpler test case for pixel selection
    tasks = []

    # Create items with sequential timestamps
    for i in range(4):
        date = datetime.datetime(2023, 1, 1) + datetime.timedelta(days=i)

        def make_task(value):
            def task():
                array = np.ma.MaskedArray(
                    data=np.ones((1, 5, 5)) * value,
                    mask=np.zeros((1, 5, 5), dtype=bool),
                )
                return ImageData(array)

            return task

        asset = {
            "datetime": date,
            "value": 10 + i,
        }
        tasks.append((make_task(10 + i), asset))

    stack = RasterStack(
        tasks=tasks,
        timestamp_fn=lambda asset: asset["datetime"],
    )

    # Verify keys are datetime objects in temporal order
    keys = list(stack.keys())
    assert len(keys) == 4
    assert all(isinstance(k, datetime.datetime) for k in keys)

    # First key should be earliest date
    assert keys[0] == datetime.datetime(2023, 1, 1)
    # Last key should be latest date
    assert keys[-1] == datetime.datetime(2023, 1, 4)


def test_first_last_temporal_behavior():
    """Test that first/last functions correctly handle temporal data."""

    # Create a simple temporal stack to test first/last functions
    # Using datetime keys directly
    images = {}

    # Create temporal data where each timestamp has one image
    for i in range(3):
        date = datetime.datetime(2021, 1, 1 + i)
        data = np.ma.array(
            np.ones((3, 10, 10), dtype=np.float32) * (i + 1),
            mask=np.zeros((3, 10, 10), dtype=bool),
        )
        images[date] = ImageData(data, band_descriptions=["red", "green", "blue"])

    # Convert to RasterStack
    stack = RasterStack.from_images(images)

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
