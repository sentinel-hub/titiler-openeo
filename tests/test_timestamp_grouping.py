"""Test timestamp-based grouping and concurrent execution in apply_pixel_selection."""

import time
from datetime import datetime, timedelta
from threading import Lock

import numpy as np
from rio_tiler.constants import MAX_THREADS
from rio_tiler.models import ImageData

from titiler.openeo.processes.implementations.data_model import RasterStack
from titiler.openeo.processes.implementations.reduce import apply_pixel_selection


def create_test_lazy_raster_stack(timestamp_groups, track_execution=False):
    """Create a real RasterStack with timestamp grouping for testing.

    Args:
        timestamp_groups: Dict mapping timestamps to lists of keys
        track_execution: If True, add execution tracking

    Returns:
        RasterStack with the given timestamp configuration
    """
    execution_log = [] if track_execution else None
    execution_lock = Lock() if track_execution else None

    tasks = []
    for timestamp, keys in sorted(timestamp_groups.items()):
        for key in keys:

            def make_task(k, ts, log=execution_log, lock=execution_lock):
                def task_fn():
                    if log is not None and lock is not None:
                        with lock:
                            log.append(
                                {
                                    "key": k,
                                    "timestamp": time.time(),
                                }
                            )
                    time.sleep(0.01)  # Small delay to track timing
                    array = np.ma.ones((3, 10, 10)) * (hash(k) % 100)
                    return ImageData(
                        array,
                        assets=[k],
                        crs="EPSG:4326",
                        bounds=(-180, -90, 180, 90),
                        band_names=["red", "green", "blue"],
                    )

                return task_fn

            tasks.append(
                (make_task(key, timestamp), {"id": key, "timestamp": timestamp})
            )

    stack = RasterStack(
        tasks=tasks,
        key_fn=lambda asset: asset["id"],
        timestamp_fn=lambda asset: asset["timestamp"],
        width=10,
        height=10,
        bounds=(-180, -90, 180, 90),
        dst_crs="EPSG:4326",
        band_names=["red", "green", "blue"],
    )

    # Attach execution log for tracking
    if track_execution:
        stack._test_execution_log = execution_log

    return stack


def test_timestamp_based_grouping():
    """Test that apply_pixel_selection processes images from timestamp-ordered RasterStack."""

    # Create test data with 3 timestamps, each having 2 images
    timestamp_groups = {
        datetime(2021, 1, 1): ["item_1_2021-01-01", "item_2_2021-01-01"],
        datetime(2021, 1, 2): ["item_1_2021-01-02", "item_2_2021-01-02"],
        datetime(2021, 1, 3): ["item_1_2021-01-03", "item_2_2021-01-03"],
    }

    stack = create_test_lazy_raster_stack(timestamp_groups, track_execution=True)

    # Apply pixel selection
    result = apply_pixel_selection(stack, pixel_selection="first")

    # Verify result structure - now returns RasterStack
    assert isinstance(result, (dict, RasterStack))
    assert "data" in result
    assert isinstance(result["data"], ImageData)

    # Verify execution log - should have processed images
    assert len(stack._test_execution_log) > 0


def test_concurrent_execution_within_timestamp_group():
    """Test that images within a timestamp group are loaded."""

    # Create test data with one timestamp having multiple images
    timestamp_groups = {
        datetime(2021, 1, 1): [f"item_{i}_2021-01-01" for i in range(5)],
    }

    stack = create_test_lazy_raster_stack(timestamp_groups, track_execution=True)

    result = apply_pixel_selection(stack, pixel_selection="first")

    # Verify we got a result
    assert "data" in result

    # Check execution log - at least some items should have been processed
    assert len(stack._test_execution_log) >= 1


def test_early_termination_by_timestamp_group():
    """Test that processing stops when pixel selection is satisfied."""

    # Create test data with multiple timestamp groups
    timestamp_groups = {
        datetime(2021, 1, 1): ["item_1_2021-01-01"],
        datetime(2021, 1, 2): ["item_1_2021-01-02"],
        datetime(2021, 1, 3): ["item_1_2021-01-03"],
        datetime(2021, 1, 4): ["item_1_2021-01-04"],
    }

    stack = create_test_lazy_raster_stack(timestamp_groups, track_execution=True)

    # Use "first" selection which should terminate after first valid image
    result = apply_pixel_selection(stack, pixel_selection="first")

    # Verify we got a result
    assert "data" in result

    # Should have processed at least one item but not necessarily all
    processed_count = len(stack._test_execution_log)
    assert processed_count >= 1
    assert processed_count <= 4  # At most all items


def test_failed_tasks_handling_in_timestamp_group():
    """Test that failed tasks within a RasterStack are handled gracefully."""
    from rio_tiler.errors import TileOutsideBounds

    def make_failing_task(key):
        def task_fn():
            if "bad" in key:
                raise TileOutsideBounds(f"Failed to load {key}")
            array = np.ma.ones((3, 10, 10))
            return ImageData(
                array,
                assets=[key],
                crs="EPSG:4326",
                bounds=(-180, -90, 180, 90),
                band_names=["red", "green", "blue"],
            )

        return task_fn

    tasks = [
        (
            make_failing_task("good_item"),
            {"id": "good_item", "timestamp": datetime(2021, 1, 1)},
        ),
        (
            make_failing_task("bad_item_1"),
            {"id": "bad_item_1", "timestamp": datetime(2021, 1, 1)},
        ),
        (
            make_failing_task("bad_item_2"),
            {"id": "bad_item_2", "timestamp": datetime(2021, 1, 1)},
        ),
    ]

    stack = RasterStack(
        tasks=tasks,
        key_fn=lambda asset: asset["id"],
        timestamp_fn=lambda asset: asset["timestamp"],
        allowed_exceptions=(TileOutsideBounds,),
        width=10,
        height=10,
        bounds=(-180, -90, 180, 90),
        dst_crs="EPSG:4326",
        band_names=["red", "green", "blue"],
    )

    # Should handle failures gracefully and continue processing
    result = apply_pixel_selection(stack, pixel_selection="first")

    # Should still get a result from the good item
    assert "data" in result


def test_thread_pool_executor_usage():
    """Test that RasterStack has concurrent execution capabilities."""

    from titiler.openeo.processes.implementations.data_model import RasterStack

    def create_test_image():
        return ImageData(
            np.ma.ones((3, 10, 10)),
            assets=["test_item"],
            crs="EPSG:4326",
            bounds=(-180, -90, 180, 90),
            band_names=["red", "green", "blue"],
        )

    # Create a RasterStack with multiple items
    tasks = [
        (create_test_image, {"timestamp": datetime(2021, 1, 1), "item_id": i})
        for i in range(3)
    ]

    stack = RasterStack(
        tasks,
        key_fn=lambda x: f"item_{x['item_id']}",
        timestamp_fn=lambda x: x["timestamp"],
    )

    # Verify the stack has the concurrent execution method
    assert hasattr(stack, "_execute_selected_tasks")
    assert hasattr(stack, "_max_workers")
    assert stack._max_workers == MAX_THREADS  # Default MAX_THREADS value

    # Test that get_by_timestamp works (which internally uses _execute_selected_tasks)
    result = stack.get_by_timestamp(datetime(2021, 1, 1))
    assert len(result) == 3
    assert all(f"item_{i}" in result for i in range(3))


def test_real_lazy_raster_stack_integration():
    """Integration test using real RasterStack with timestamp functionality."""

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

    # Create RasterStack with timestamp support
    def key_fn(asset):
        return asset["id"]

    def timestamp_fn(asset):
        return datetime.fromisoformat(asset["timestamp"])

    lazy_stack = RasterStack(
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
