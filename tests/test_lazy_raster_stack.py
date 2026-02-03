"""Test RasterStack with apply_pixel_selection."""

from datetime import datetime, timezone

import numpy as np
import pytest
from rio_tiler.models import ImageData

from titiler.openeo.processes.implementations.data_model import RasterStack
from titiler.openeo.processes.implementations.reduce import apply_pixel_selection


# Create a mock task that returns an ImageData
def mock_task():
    """Mock task that returns an ImageData."""
    # Create a simple 1-band image
    array = np.ma.MaskedArray(
        data=np.ones((1, 10, 10)), mask=np.zeros((1, 10, 10), dtype=bool)
    )
    return ImageData(array)


def test_lazy_raster_stack():
    # Create a mock asset with datetime
    dt = datetime(2021, 1, 1, tzinfo=timezone.utc)
    mock_asset = {"datetime": dt}

    # Create a list of tasks
    tasks = [(mock_task, mock_asset)]

    # Create a RasterStack
    lazy_stack = RasterStack(
        tasks=tasks,
        timestamp_fn=lambda asset: asset["datetime"],
    )

    assert len(lazy_stack) > 0
    # Check that no tasks have been executed yet (cache is empty)
    assert len(lazy_stack._data_cache) == 0

    # Apply pixel selection
    result = apply_pixel_selection(lazy_stack, pixel_selection="first")

    assert isinstance(result, dict)  # RasterStack is Dict[datetime, ImageData]
    assert isinstance(result.first, ImageData)
    # Check that tasks have been executed (cache is populated)
    assert len(lazy_stack._data_cache) > 0


def test_lazy_raster_stack_same_timestamp():
    """Test that RasterStack handles items with the same timestamp.

    Since keys are now datetime objects directly, items with the same
    timestamp will have the same key (last one wins in the mapping).
    """
    dt = datetime(2021, 1, 1, tzinfo=timezone.utc)
    mock_asset_1 = {"datetime": dt}
    mock_asset_2 = {"datetime": dt}

    # Create tasks
    tasks = [(mock_task, mock_asset_1), (mock_task, mock_asset_2)]

    # Create a RasterStack
    lazy_stack = RasterStack(
        tasks=tasks,
        timestamp_fn=lambda asset: asset["datetime"],
    )

    # Since both have the same timestamp (key), only one will be stored
    # This is expected behavior - timestamps must be unique
    assert len(lazy_stack) == 2  # Both in _keys list
    assert dt in lazy_stack

    # Test timestamp
    timestamps = lazy_stack.timestamps()
    assert len(timestamps) == 2
    assert isinstance(timestamps[0], datetime)
    assert timestamps[0].year == 2021


def test_lazy_raster_stack_temporal_ordering():
    """Test that RasterStack returns data in temporal order."""
    # Test data with timestamps in mixed order
    dt1 = datetime(2023, 6, 10)  # earliest
    dt2 = datetime(2023, 6, 15)  # middle
    dt3 = datetime(2023, 6, 20)  # latest

    assets = [
        {"datetime": dt2},  # middle timestamp (inserted first)
        {"datetime": dt1},  # earliest timestamp
        {"datetime": dt3},  # latest timestamp
    ]

    # Create tasks using the same pattern as other tests
    tasks = [(mock_task, asset) for asset in assets]

    lazy_stack = RasterStack(
        tasks=tasks,
        timestamp_fn=lambda asset: asset["datetime"],
    )

    # Keys (timestamps) should be in temporal order, not insertion order
    keys = list(lazy_stack.keys())
    assert keys == [dt1, dt2, dt3]  # Chronological order

    # Iteration methods should maintain temporal order
    assert list(lazy_stack.keys()) == [dt1, dt2, dt3]


def test_truly_lazy_execution():
    """Test that RasterStack only executes tasks when specifically requested."""

    # Create a counter to track how many times tasks are executed
    execution_counter = {"count": 0}

    def counting_mock_task():
        """Mock task that increments a counter when executed."""
        execution_counter["count"] += 1
        array = np.ma.MaskedArray(
            data=np.ones((1, 10, 10)) * execution_counter["count"],
            mask=np.zeros((1, 10, 10), dtype=bool),
        )
        return ImageData(array)

    # Create multiple mock assets with different timestamps
    dt1 = datetime(2021, 1, 1, tzinfo=timezone.utc)
    dt2 = datetime(2021, 1, 2, tzinfo=timezone.utc)
    dt3 = datetime(2021, 1, 3, tzinfo=timezone.utc)

    assets = [
        {"datetime": dt1},
        {"datetime": dt2},
        {"datetime": dt3},
    ]

    # Create tasks
    tasks = [(counting_mock_task, asset) for asset in assets]

    # Create RasterStack
    lazy_stack = RasterStack(
        tasks=tasks,
        timestamp_fn=lambda asset: asset["datetime"],
    )

    # Initially, no tasks should be executed
    assert execution_counter["count"] == 0
    assert len(lazy_stack._data_cache) == 0

    # Check that we can inspect the stack without executing tasks
    assert len(lazy_stack) == 3
    assert dt1 in lazy_stack
    assert list(lazy_stack.keys()) == [dt1, dt2, dt3]
    assert execution_counter["count"] == 0  # Still no execution

    # Access one item - should execute only that task
    item1 = lazy_stack[dt1]
    assert execution_counter["count"] == 1  # Only one task executed
    assert len(lazy_stack._data_cache) == 1
    assert dt1 in lazy_stack._data_cache
    assert isinstance(item1, ImageData)

    # Access the same item again - should not re-execute (cached)
    item1_again = lazy_stack[dt1]
    assert execution_counter["count"] == 1  # Still only one execution
    assert item1 is item1_again  # Same object from cache

    # Access a different item - should execute only that task
    _ = lazy_stack[dt2]
    assert execution_counter["count"] == 2  # Now two tasks executed
    assert len(lazy_stack._data_cache) == 2
    assert dt2 in lazy_stack._data_cache

    # Access dt3 - should execute only that task
    _ = lazy_stack[dt3]
    assert execution_counter["count"] == 3  # Now all three tasks executed

    # Test that all items are now cached
    assert len(lazy_stack._data_cache) == 3


def test_lazy_raster_stack_first_last_properties():
    """Test the first and last properties for efficient RasterStack access."""
    dt1 = datetime(2021, 1, 1, tzinfo=timezone.utc)
    dt2 = datetime(2021, 1, 2, tzinfo=timezone.utc)
    dt3 = datetime(2021, 1, 3, tzinfo=timezone.utc)

    assets = [
        {"datetime": dt1},
        {"datetime": dt2},
        {"datetime": dt3},
    ]

    tasks = [(mock_task, asset) for asset in assets]

    lazy_stack = RasterStack(
        tasks=tasks,
        timestamp_fn=lambda asset: asset["datetime"],
    )

    # Test first property (should only execute first task)
    first_item = lazy_stack.first
    assert isinstance(first_item, ImageData)
    assert len(lazy_stack._data_cache) == 1
    assert dt1 in lazy_stack._data_cache

    # Test last property (should execute last task)
    last_item = lazy_stack.last
    assert isinstance(last_item, ImageData)
    assert len(lazy_stack._data_cache) == 2  # First and last now cached
    assert dt3 in lazy_stack._data_cache

    # Test from_images factory method
    single_img = mock_task()
    dt_now = datetime.now()
    stack_from_img = RasterStack.from_images({dt_now: single_img})
    assert isinstance(stack_from_img, RasterStack)
    assert dt_now in stack_from_img
    assert stack_from_img[dt_now].array.shape == single_img.array.shape


def test_lazy_raster_stack_error_handling():
    """Test error handling in RasterStack."""
    from rio_tiler.errors import TileOutsideBounds

    # Create a task that will fail
    def failing_task():
        raise TileOutsideBounds("Test error")

    # Create a task that will succeed
    def success_task():
        return mock_task()

    dt1 = datetime(2021, 1, 1, tzinfo=timezone.utc)
    dt2 = datetime(2021, 1, 2, tzinfo=timezone.utc)

    assets = [
        {"datetime": dt1},  # failing
        {"datetime": dt2},  # success
    ]

    tasks = [(failing_task, assets[0]), (success_task, assets[1])]

    lazy_stack = RasterStack(
        tasks=tasks,
        timestamp_fn=lambda asset: asset["datetime"],
        allowed_exceptions=(TileOutsideBounds,),
    )

    # Test that accessing a failing task raises KeyError
    with pytest.raises(KeyError, match="Task execution failed"):
        _ = lazy_stack[dt1]

    # Test that successful task still works
    success_img = lazy_stack[dt2]
    assert isinstance(success_img, ImageData)

    # Test accessing non-existent key
    non_existent = datetime(2099, 1, 1)
    with pytest.raises(KeyError, match="not found in RasterStack"):
        _ = lazy_stack[non_existent]

    # Test get method with non-existent key
    assert lazy_stack.get(non_existent) is None
    assert lazy_stack.get(non_existent, "default") == "default"


def test_lazy_raster_stack_selective_execution():
    """Test selective task execution methods."""
    execution_order = []

    def tracking_task(task_id):
        def task():
            execution_order.append(task_id)
            return mock_task()

        return task

    dt1 = datetime(2021, 1, 1, tzinfo=timezone.utc)
    dt2 = datetime(2021, 1, 2, tzinfo=timezone.utc)
    dt3 = datetime(2021, 1, 3, tzinfo=timezone.utc)
    dt4 = datetime(2021, 1, 4, tzinfo=timezone.utc)

    assets = [
        {"datetime": dt1, "id": "item-001"},
        {"datetime": dt2, "id": "item-002"},
        {"datetime": dt3, "id": "item-003"},
        {"datetime": dt4, "id": "item-004"},
    ]

    tasks = [(tracking_task(asset["id"]), asset) for asset in assets]

    lazy_stack = RasterStack(
        tasks=tasks,
        timestamp_fn=lambda asset: asset["datetime"],
    )

    # Test selective execution by direct access
    _ = lazy_stack[dt1]
    assert execution_order == ["item-001"]

    _ = lazy_stack[dt3]
    assert execution_order == ["item-001", "item-003"]

    # Access remaining items via values()
    _ = lazy_stack.values()
    assert len(execution_order) == 4
    assert set(execution_order) == {"item-001", "item-002", "item-003", "item-004"}


def test_lazy_raster_stack_timestamps():
    """Test timestamp-related functionality."""
    dt1 = datetime(2021, 1, 1, tzinfo=timezone.utc)  # Earliest
    dt2 = datetime(2021, 1, 2, tzinfo=timezone.utc)  # Middle
    dt3 = datetime(2021, 1, 3, tzinfo=timezone.utc)  # Latest

    # Insert in non-chronological order
    assets = [
        {"datetime": dt3},  # Latest first
        {"datetime": dt1},  # Earliest
        {"datetime": dt2},  # Middle
    ]

    tasks = [(mock_task, asset) for asset in assets]

    lazy_stack = RasterStack(
        tasks=tasks,
        timestamp_fn=lambda asset: asset["datetime"],
    )

    # Test temporal ordering (keys should be sorted by timestamp)
    keys = list(lazy_stack.keys())
    assert keys == [dt1, dt2, dt3]  # Chronological order

    # Test timestamps method (same as keys for datetime-keyed stack)
    timestamps = lazy_stack.timestamps()
    assert len(timestamps) == 3
    assert timestamps[0] == dt1
    assert timestamps[1] == dt2
    assert timestamps[2] == dt3


def test_lazy_raster_stack_max_workers():
    """Test RasterStack with different max_workers settings."""
    dt1 = datetime(2021, 1, 1)
    dt2 = datetime(2021, 1, 2)

    assets = [
        {"datetime": dt1},
        {"datetime": dt2},
    ]

    tasks = [(mock_task, asset) for asset in assets]

    # Test with different max_workers values
    for max_workers in [1, 2, 4]:
        lazy_stack = RasterStack(
            tasks=tasks,
            timestamp_fn=lambda asset: asset["datetime"],
            max_workers=max_workers,
        )

        assert lazy_stack._max_workers == max_workers

        # Test that it still works correctly
        item = lazy_stack[dt1]
        assert isinstance(item, ImageData)


def test_lazy_raster_stack_future_tasks():
    """Test RasterStack with Future-based tasks."""
    from concurrent.futures import ThreadPoolExecutor

    def slow_task():
        return mock_task()

    dt1 = datetime(2021, 1, 1)
    dt2 = datetime(2021, 1, 2)

    assets = [
        {"datetime": dt1},
        {"datetime": dt2},
    ]

    # Create Future-based tasks
    tasks = []
    with ThreadPoolExecutor(max_workers=2) as executor:
        for asset in assets:
            future = executor.submit(slow_task)
            tasks.append((future, asset))

        lazy_stack = RasterStack(
            tasks=tasks,
            timestamp_fn=lambda asset: asset["datetime"],
        )

        # Access items (should work with Future tasks)
        item1 = lazy_stack[dt1]
        item2 = lazy_stack[dt2]

        assert isinstance(item1, ImageData)
        assert isinstance(item2, ImageData)
        assert len(lazy_stack._data_cache) == 2


def test_lazy_raster_stack_iteration_methods():
    """Test iteration methods of RasterStack."""
    dt1 = datetime(2021, 1, 1, tzinfo=timezone.utc)
    dt2 = datetime(2021, 1, 2, tzinfo=timezone.utc)

    assets = [
        {"datetime": dt1},
        {"datetime": dt2},
    ]

    tasks = [(mock_task, asset) for asset in assets]

    lazy_stack = RasterStack(
        tasks=tasks,
        timestamp_fn=lambda asset: asset["datetime"],
    )

    # Test iteration without executing tasks
    assert len(lazy_stack._data_cache) == 0

    # Test keys()
    keys = list(lazy_stack.keys())
    assert keys == [dt1, dt2]
    assert len(lazy_stack._data_cache) == 0  # No tasks executed

    # Test __iter__
    iter_keys = list(lazy_stack)
    assert iter_keys == [dt1, dt2]
    assert len(lazy_stack._data_cache) == 0  # No tasks executed

    # Test __contains__
    assert dt1 in lazy_stack
    assert datetime(2099, 1, 1) not in lazy_stack
    assert len(lazy_stack._data_cache) == 0  # No tasks executed

    # Test values() - should execute all tasks
    values = list(lazy_stack.values())
    assert len(values) == 2
    assert all(isinstance(v, ImageData) for v in values)
    assert len(lazy_stack._data_cache) == 2  # All tasks executed

    # Test items() - should use cached values
    items = list(lazy_stack.items())
    assert len(items) == 2
    assert all(isinstance(v, ImageData) for k, v in items)
    assert all(k in [dt1, dt2] for k, v in items)
    assert len(lazy_stack._data_cache) == 2  # No new executions


def test_lazy_raster_stack_execute_all_tasks():
    """Test the _execute_all_tasks method for backward compatibility."""
    execution_count = {"count": 0}

    def counting_task():
        execution_count["count"] += 1
        return mock_task()

    dt1 = datetime(2021, 1, 1)
    dt2 = datetime(2021, 1, 2)
    dt3 = datetime(2021, 1, 3)

    assets = [
        {"datetime": dt1},
        {"datetime": dt2},
        {"datetime": dt3},
    ]

    tasks = [(counting_task, asset) for asset in assets]

    lazy_stack = RasterStack(
        tasks=tasks,
        timestamp_fn=lambda asset: asset["datetime"],
    )

    # Initially no tasks executed
    assert execution_count["count"] == 0
    assert len(lazy_stack._data_cache) == 0

    # Call _execute_all_tasks directly
    lazy_stack._execute_all_tasks()

    # All tasks should now be executed
    assert execution_count["count"] == 3
    assert len(lazy_stack._data_cache) == 3
    assert all(key in lazy_stack._data_cache for key in [dt1, dt2, dt3])


def test_lazy_raster_stack_edge_cases():
    """Test various edge cases and boundary conditions."""

    # Test empty task list
    empty_lazy_stack = RasterStack(
        tasks=[],
        timestamp_fn=lambda asset: asset["datetime"],
    )

    assert len(empty_lazy_stack) == 0
    assert list(empty_lazy_stack.keys()) == []
    assert list(empty_lazy_stack.values()) == []
    assert list(empty_lazy_stack.items()) == []
    assert len(empty_lazy_stack.timestamps()) == 0


def test_first_property_with_failing_tasks():
    """Test .first property finds the first successful task when early tasks fail."""
    from rio_tiler.errors import TileOutsideBounds

    def create_failing_task():
        def task():
            raise TileOutsideBounds("Task failed")

        return task

    def create_successful_task(value):
        def task():
            array = np.ma.MaskedArray(
                data=np.full((1, 10, 10), value), mask=np.zeros((1, 10, 10), dtype=bool)
            )
            return ImageData(array)

        return task

    dt1 = datetime(2021, 1, 1, tzinfo=timezone.utc)
    dt2 = datetime(2021, 1, 2, tzinfo=timezone.utc)
    dt3 = datetime(2021, 1, 3, tzinfo=timezone.utc)

    # Create tasks where first 2 fail and 3rd succeeds
    assets = [
        {"datetime": dt1},
        {"datetime": dt2},
        {"datetime": dt3},
    ]

    tasks = [
        (create_failing_task(), assets[0]),
        (create_failing_task(), assets[1]),
        (create_successful_task(42), assets[2]),
    ]

    lazy_stack = RasterStack(
        tasks=tasks,
        timestamp_fn=lambda asset: asset["datetime"],
        allowed_exceptions=(TileOutsideBounds,),
    )

    # .first should find the first successful task (the 3rd one)
    result = lazy_stack.first
    assert isinstance(result, ImageData)
    assert result.array[0, 0, 0] == 42  # Value from the successful task


def test_first_property_all_tasks_fail():
    """Test .first property when all tasks fail."""
    from rio_tiler.errors import TileOutsideBounds

    def create_failing_task():
        def task():
            raise TileOutsideBounds("Task failed")

        return task

    dt1 = datetime(2021, 1, 1)
    dt2 = datetime(2021, 1, 2)

    assets = [{"datetime": dt1}, {"datetime": dt2}]
    tasks = [(create_failing_task(), asset) for asset in assets]

    lazy_stack = RasterStack(
        tasks=tasks,
        timestamp_fn=lambda asset: asset["datetime"],
        allowed_exceptions=(TileOutsideBounds,),
    )

    # .first should raise KeyError when all tasks fail
    with pytest.raises(KeyError, match="No successful tasks found"):
        _ = lazy_stack.first


def test_temporal_sorting_preserves_task_mapping():
    """Test that temporal sorting doesn't break key-to-task mapping."""

    def create_task_with_value(value):
        def task():
            array = np.ma.MaskedArray(
                data=np.full((1, 10, 10), value), mask=np.zeros((1, 10, 10), dtype=bool)
            )
            return ImageData(array)

        return task

    # Create tasks with REVERSE chronological order (newest first)
    dates = [
        datetime(2023, 1, 5),
        datetime(2023, 1, 3),
        datetime(2023, 1, 1),
        datetime(2023, 1, 4),
        datetime(2023, 1, 2),
    ]
    tasks = []

    for i, dt in enumerate(dates):
        asset = {"datetime": dt}
        task = create_task_with_value(i * 10)  # Values: 0, 10, 20, 30, 40
        tasks.append((task, asset))

    lazy_stack = RasterStack(
        tasks=tasks,
        timestamp_fn=lambda asset: asset["datetime"],
    )

    # Keys should be in temporal order (chronological)
    expected_temporal_order = [
        datetime(2023, 1, 1),
        datetime(2023, 1, 2),
        datetime(2023, 1, 3),
        datetime(2023, 1, 4),
        datetime(2023, 1, 5),
    ]
    assert list(lazy_stack.keys()) == expected_temporal_order

    # Each key should return the correct value from its original task
    # dates[2] = 2023-01-01, value = 20
    # dates[4] = 2023-01-02, value = 40
    # dates[1] = 2023-01-03, value = 10
    # dates[3] = 2023-01-04, value = 30
    # dates[0] = 2023-01-05, value = 0
    assert lazy_stack[datetime(2023, 1, 1)].array[0, 0, 0] == 20
    assert lazy_stack[datetime(2023, 1, 2)].array[0, 0, 0] == 40
    assert lazy_stack[datetime(2023, 1, 3)].array[0, 0, 0] == 10
    assert lazy_stack[datetime(2023, 1, 4)].array[0, 0, 0] == 30
    assert lazy_stack[datetime(2023, 1, 5)].array[0, 0, 0] == 0


def test_apply_pixel_selection_with_failing_tasks():
    """Test apply_pixel_selection works when some tasks fail during pixel selection processing."""
    from rio_tiler.errors import TileOutsideBounds

    def create_failing_task():
        def task():
            raise TileOutsideBounds("Task failed")

        return task

    def create_successful_task(value):
        def task():
            array = np.ma.MaskedArray(
                data=np.full((1, 10, 10), value), mask=np.zeros((1, 10, 10), dtype=bool)
            )
            return ImageData(array)

        return task

    dt1 = datetime(2021, 1, 1, tzinfo=timezone.utc)
    dt2 = datetime(2021, 1, 2, tzinfo=timezone.utc)
    dt3 = datetime(2021, 1, 3, tzinfo=timezone.utc)

    # Create tasks where first 2 fail and 3rd succeeds
    assets = [
        {"datetime": dt1},
        {"datetime": dt2},
        {"datetime": dt3},
    ]

    tasks = [
        (create_failing_task(), assets[0]),
        (create_failing_task(), assets[1]),
        (create_successful_task(42), assets[2]),
    ]

    lazy_stack = RasterStack(
        tasks=tasks,
        timestamp_fn=lambda asset: asset["datetime"],
        allowed_exceptions=(TileOutsideBounds,),
    )

    # apply_pixel_selection should process available images for pixel selection
    # It will skip failed tasks and process successful ones
    result = apply_pixel_selection(lazy_stack, pixel_selection="first")

    assert isinstance(result, dict)
    assert isinstance(result.first, ImageData)
    # The result should contain pixel selection from successful images
    assert result.first.array[0, 0, 0] == 42  # Value from the successful task


def test_empty_lazy_raster_stack():
    """Test RasterStack with empty tasks list."""
    # Create empty RasterStack
    lazy_stack = RasterStack(
        tasks=[],
        timestamp_fn=lambda asset: asset["datetime"],
    )

    assert len(lazy_stack) == 0
    assert list(lazy_stack.keys()) == []
    assert list(lazy_stack.values()) == []
    assert list(lazy_stack.items()) == []

    # .first should raise error on empty stack
    with pytest.raises(KeyError, match="RasterStack is empty"):
        _ = lazy_stack.first

    # apply_pixel_selection should also fail gracefully
    with pytest.raises(ValueError, match="Method returned an empty array"):
        apply_pixel_selection(lazy_stack, pixel_selection="first")
