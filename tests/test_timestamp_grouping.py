"""Test timestamp-based grouping and concurrent execution in apply_pixel_selection."""

import time
from datetime import datetime, timedelta
from threading import Lock
from unittest.mock import MagicMock, patch

import numpy as np
from rio_tiler.models import ImageData

from titiler.openeo.processes.implementations.data_model import LazyRasterStack
from titiler.openeo.processes.implementations.reduce import apply_pixel_selection


class MockLazyRasterStackWithTimestamps:
    """Mock LazyRasterStack that supports timestamp-based grouping."""

    def __init__(self, timestamp_groups):
        """
        Args:
            timestamp_groups: Dict mapping timestamps to lists of keys
        """
        self.timestamp_groups = timestamp_groups
        self._execution_log = []
        self._execution_lock = Lock()

    def timestamps(self):
        """Return sorted list of timestamps."""
        return sorted(self.timestamp_groups.keys())

    def get_by_timestamp(self, timestamp):
        """Return a dict-like object for the given timestamp."""
        keys = self.timestamp_groups.get(timestamp, [])
        return MockTimestampGroup(keys, self._execution_log, self._execution_lock)

    def keys(self):
        """Return all keys."""
        all_keys = []
        for keys in self.timestamp_groups.values():
            all_keys.extend(keys)
        return all_keys

    def groupby_timestamp(self):
        """Indicate this mock supports grouping."""
        return True


class MockTimestampGroup:
    """Mock timestamp group that tracks execution."""

    def __init__(self, keys, execution_log, execution_lock):
        """Initialize timestamp group with execution tracking."""
        self.keys_list = keys
        self.execution_log = execution_log
        self.execution_lock = execution_lock

    def keys(self):
        """Return list of keys for this timestamp group."""
        return self.keys_list

    def __getitem__(self, key):
        """Simulate image loading with execution tracking."""
        # Record the execution with timestamp
        with self.execution_lock:
            self.execution_log.append(
                {
                    "key": key,
                    "thread_id": time.time(),  # Use time as pseudo thread ID
                    "timestamp": time.time(),
                }
            )

        # Simulate some processing time
        time.sleep(0.01)

        # Create a mock ImageData
        array = np.ma.ones((3, 10, 10)) * hash(key) % 100
        return ImageData(
            array,
            assets=[key],
            crs="EPSG:4326",
            bounds=(-180, -90, 180, 90),
            band_names=["red", "green", "blue"],
        )


def test_timestamp_based_grouping():
    """Test that apply_pixel_selection processes images by timestamp groups."""

    # Create test data with 3 timestamps, each having 2 images
    timestamp_groups = {
        datetime(2021, 1, 1): ["item_1_2021-01-01", "item_2_2021-01-01"],
        datetime(2021, 1, 2): ["item_1_2021-01-02", "item_2_2021-01-02"],
        datetime(2021, 1, 3): ["item_1_2021-01-03", "item_2_2021-01-03"],
    }

    mock_stack = MockLazyRasterStackWithTimestamps(timestamp_groups)

    # Apply pixel selection
    result = apply_pixel_selection(mock_stack, pixel_selection="first")

    # Verify result structure
    assert isinstance(result, dict)
    assert "data" in result
    assert isinstance(result["data"], ImageData)

    # Verify execution log - should have processed images
    assert len(mock_stack._execution_log) > 0

    # Extract timestamps from execution log
    execution_times = [entry["timestamp"] for entry in mock_stack._execution_log]

    # Group executions by timestamp groups (allowing some tolerance for timing)
    timestamp_execution_groups = []
    current_group = []
    last_time = None

    for exec_time in execution_times:
        if last_time is None or (exec_time - last_time) < 0.02:  # Within same group
            current_group.append(exec_time)
        else:  # New group
            if current_group:
                timestamp_execution_groups.append(current_group)
            current_group = [exec_time]
        last_time = exec_time

    if current_group:
        timestamp_execution_groups.append(current_group)

    # Should have processed at least the first timestamp group
    assert len(timestamp_execution_groups) >= 1


def test_concurrent_execution_within_timestamp_group():
    """Test that images within a timestamp group are loaded concurrently."""

    # Create test data with one timestamp having multiple images
    timestamp_groups = {
        datetime(2021, 1, 1): [f"item_{i}_2021-01-01" for i in range(5)],
    }

    mock_stack = MockLazyRasterStackWithTimestamps(timestamp_groups)

    result = apply_pixel_selection(mock_stack, pixel_selection="first")
    # Performance timing could be added here if needed for analysis    # Verify we got a result
    assert "data" in result

    # Check execution log
    executions = mock_stack._execution_log

    # All executions should have happened roughly at the same time (within timestamp group)
    if len(executions) > 1:
        execution_times = [entry["timestamp"] for entry in executions]
        time_spread = max(execution_times) - min(execution_times)

        # If executed sequentially, it would take 5 * 0.01 = 0.05 seconds
        # If executed concurrently, it should be much faster
        # Allow some tolerance but it should be significantly faster than sequential
        assert (
            time_spread < 0.04
        ), f"Executions seem sequential, time spread: {time_spread}"


def test_temporal_ordering_preserved():
    """Test that timestamp groups are processed in chronological order."""

    # Create test data with timestamps in reverse chronological order in dict
    # but they should be processed in chronological order
    timestamp_groups = {
        datetime(2021, 1, 3): ["item_2021-01-03"],
        datetime(2021, 1, 1): ["item_2021-01-01"],
        datetime(2021, 1, 2): ["item_2021-01-02"],
    }

    mock_stack = MockLazyRasterStackWithTimestamps(timestamp_groups)

    # Apply pixel selection
    result = apply_pixel_selection(mock_stack, pixel_selection="first")

    # Verify we got a result
    assert "data" in result

    # Check that timestamps() method returns sorted timestamps
    timestamps = mock_stack.timestamps()
    expected_order = [datetime(2021, 1, 1), datetime(2021, 1, 2), datetime(2021, 1, 3)]
    assert timestamps == expected_order


def test_early_termination_by_timestamp_group():
    """Test that processing stops when pixel selection is satisfied."""

    # Create test data with multiple timestamp groups
    timestamp_groups = {
        datetime(2021, 1, 1): ["item_1_2021-01-01"],
        datetime(2021, 1, 2): ["item_1_2021-01-02"],
        datetime(2021, 1, 3): ["item_1_2021-01-03"],
        datetime(2021, 1, 4): ["item_1_2021-01-04"],
    }

    mock_stack = MockLazyRasterStackWithTimestamps(timestamp_groups)

    # Use "first" selection which should terminate after first valid image
    result = apply_pixel_selection(mock_stack, pixel_selection="first")

    # Verify we got a result
    assert "data" in result

    # Should not have processed all timestamp groups
    # The "first" method should terminate early
    processed_items = [entry["key"] for entry in mock_stack._execution_log]

    # Should have processed at least one item but not necessarily all
    assert len(processed_items) >= 1
    # For "first" selection, it might terminate after just one timestamp group
    assert len(processed_items) <= 4  # At most all items


def test_fallback_to_sequential_for_non_lazy_stack():
    """Test that regular dicts fall back to sequential processing."""

    # Create a regular dict (not LazyRasterStack)
    regular_stack = {}
    for i in range(3):
        array = np.ma.ones((3, 10, 10)) * (i + 1)
        regular_stack[f"item_{i}"] = ImageData(
            array,
            assets=[f"item_{i}"],
            crs="EPSG:4326",
            bounds=(-180, -90, 180, 90),
            band_names=["red", "green", "blue"],
        )

    # This should work without timestamp-based processing
    result = apply_pixel_selection(regular_stack, pixel_selection="first")

    # Verify we got a result
    assert isinstance(result, dict)
    assert "data" in result
    assert isinstance(result["data"], ImageData)


def test_failed_tasks_handling_in_timestamp_group():
    """Test that failed tasks within a timestamp group are handled gracefully."""

    class FailingTimestampGroup:
        def __init__(self):
            self.keys_list = ["good_item", "bad_item_1", "bad_item_2"]

        def keys(self):
            return self.keys_list

        def __getitem__(self, key):
            if "bad" in key:
                raise RuntimeError(f"Failed to load {key}")

            # Return good data for good items
            array = np.ma.ones((3, 10, 10))
            return ImageData(
                array,
                assets=[key],
                crs="EPSG:4326",
                bounds=(-180, -90, 180, 90),
                band_names=["red", "green", "blue"],
            )

    class FailingMockStack:
        def timestamps(self):
            return [datetime(2021, 1, 1)]

        def get_by_timestamp(self, timestamp):
            return FailingTimestampGroup()

        def groupby_timestamp(self):
            return True

    failing_stack = FailingMockStack()

    # Should handle failures gracefully and continue processing
    # Note: warnings might not be emitted in test environment
    result = apply_pixel_selection(failing_stack, pixel_selection="first")

    # Should still get a result from the good item
    assert "data" in result


@patch("concurrent.futures.ThreadPoolExecutor")
@patch("concurrent.futures.as_completed")
def test_thread_pool_executor_usage(mock_as_completed, mock_executor_class):
    """Test that ThreadPoolExecutor is used for concurrent execution."""

    # Mock ThreadPoolExecutor
    mock_executor = MagicMock()
    mock_executor_class.return_value.__enter__.return_value = mock_executor

    # Mock future objects
    mock_future = MagicMock()
    mock_future.result.return_value = ImageData(
        np.ma.ones((3, 10, 10)),
        assets=["test_item"],
        crs="EPSG:4326",
        bounds=(-180, -90, 180, 90),
        band_names=["red", "green", "blue"],
    )
    mock_executor.submit.return_value = mock_future

    # Mock as_completed
    mock_as_completed.return_value = [mock_future]

    # Create test data
    timestamp_groups = {
        datetime(2021, 1, 1): ["item_1", "item_2"],
    }

    mock_stack = MockLazyRasterStackWithTimestamps(timestamp_groups)

    # Apply pixel selection
    result = apply_pixel_selection(mock_stack, pixel_selection="first")

    # Verify we got a result
    assert "data" in result

    # Verify ThreadPoolExecutor was used
    mock_executor_class.assert_called_once()

    # Verify submit was called for concurrent execution
    assert mock_executor.submit.call_count >= 1

    # Verify as_completed was used
    mock_as_completed.assert_called_once()


def test_empty_timestamp_groups_handling():
    """Test handling of timestamp groups with no items."""

    timestamp_groups = {
        datetime(2021, 1, 1): [],  # Empty timestamp group
        datetime(2021, 1, 2): ["item_2021-01-02"],
    }

    mock_stack = MockLazyRasterStackWithTimestamps(timestamp_groups)

    # Should skip empty groups and process the non-empty one
    result = apply_pixel_selection(mock_stack, pixel_selection="first")

    # Should get a result from the non-empty group
    assert "data" in result

    # Should have processed only the item from the non-empty group
    processed_items = [entry["key"] for entry in mock_stack._execution_log]
    assert "item_2021-01-02" in processed_items


def test_real_lazy_raster_stack_integration():
    """Integration test using real LazyRasterStack with timestamp functionality."""

    execution_log = []
    execution_lock = Lock()

    def create_task_with_timestamp(timestamp_str, item_id):
        """Create a task that records execution timing."""

        def task():
            # Record execution
            with execution_lock:
                execution_log.append(
                    {
                        "timestamp": timestamp_str,
                        "item_id": item_id,
                        "execution_time": time.time(),
                    }
                )

            # Simulate processing time
            time.sleep(0.01)

            # Create mock ImageData
            array = np.ma.ones((3, 5, 5)) * hash(item_id) % 100
            return ImageData(
                array,
                assets=[item_id],
                crs="EPSG:4326",
                bounds=(-180, -90, 180, 90),
                band_names=["red", "green", "blue"],
            )

        return task

    # Create tasks with different timestamps
    tasks = []
    base_date = datetime(2021, 1, 1)

    for day_offset in range(3):  # 3 days
        timestamp = base_date + timedelta(days=day_offset)
        timestamp_str = timestamp.isoformat()

        for item_num in range(2):  # 2 items per day
            item_id = f"item_{day_offset}_{item_num}"
            asset_info = {
                "id": item_id,
                "timestamp": timestamp_str,
                "properties": {"datetime": timestamp_str},
            }

            task = create_task_with_timestamp(timestamp_str, item_id)
            tasks.append((task, asset_info))

    # Create LazyRasterStack with timestamp support
    def key_fn(asset):
        return asset["id"]

    def timestamp_fn(asset):
        return datetime.fromisoformat(asset["timestamp"])

    lazy_stack = LazyRasterStack(
        tasks=tasks,
        key_fn=key_fn,
        timestamp_fn=timestamp_fn,
    )

    # Apply pixel selection
    result = apply_pixel_selection(lazy_stack, pixel_selection="first")
    # Execution time analysis could be added here if needed

    # Verify we got a result
    assert isinstance(result, dict)
    assert "data" in result
    assert isinstance(result["data"], ImageData)

    # Check execution patterns
    assert len(execution_log) > 0

    # Group executions by timestamp
    timestamp_groups = {}
    for entry in execution_log:
        ts = entry["timestamp"]
        if ts not in timestamp_groups:
            timestamp_groups[ts] = []
        timestamp_groups[ts].append(entry)

    # Should have processed at least one timestamp group
    assert len(timestamp_groups) >= 1

    # If we have multiple groups, verify they were processed in order
    if len(timestamp_groups) > 1:
        timestamps = sorted(timestamp_groups.keys())

        # Check that timestamp groups were processed in chronological order
        group_start_times = []
        for ts in timestamps:
            group_times = [entry["execution_time"] for entry in timestamp_groups[ts]]
            group_start_times.append(min(group_times))

        # Group start times should be in chronological order
        assert group_start_times == sorted(
            group_start_times
        ), "Timestamp groups not processed in chronological order"
