"""Test RasterStack with apply_pixel_selection."""

from datetime import datetime

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
    # Create a mock asset
    mock_asset = {"id": "item-001", "properties": {"datetime": "2021-01-01T00:00:00Z"}}

    # Create a list of tasks
    tasks = [(mock_task, mock_asset)]

    # Create a RasterStack
    lazy_stack = RasterStack(
        tasks=tasks,
        key_fn=lambda asset: asset["id"],
        timestamp_fn=lambda asset: datetime.fromisoformat(
            asset["properties"]["datetime"].replace("Z", "+00:00")
        ),
    )

    assert len(lazy_stack) > 0
    # Check that no tasks have been executed yet (cache is empty)
    assert len(lazy_stack._data_cache) == 0

    # Apply pixel selection
    result = apply_pixel_selection(lazy_stack, pixel_selection="first")

    assert isinstance(result, dict)  # RasterStack is Dict[str, ImageData]
    assert "data" in result
    assert isinstance(result["data"], ImageData)
    # Check that tasks have been executed (cache is populated)
    assert len(lazy_stack._data_cache) > 0


def test_lazy_raster_stack_same_timestamp_different_keys():
    """Test that RasterStack handles multiple items with the same timestamp value."""
    # Create mock assets with same datetime but different IDs
    # This tests that items are stored by key, not grouped by timestamp
    mock_asset_1 = {
        "id": "item-001",
        "properties": {"datetime": "2021-01-01T00:00:00Z"},
    }
    mock_asset_2 = {
        "id": "item-002",
        "properties": {"datetime": "2021-01-01T00:00:00Z"},
    }

    # Create tasks
    tasks = [(mock_task, mock_asset_1), (mock_task, mock_asset_2)]

    # Create a RasterStack
    lazy_stack = RasterStack(
        tasks=tasks,
        key_fn=lambda asset: asset["id"],
        timestamp_fn=lambda asset: datetime.fromisoformat(
            asset["properties"]["datetime"].replace("Z", "+00:00")
        ),
    )

    assert len(lazy_stack) == 2  # Should have both items
    assert "item-001" in lazy_stack
    assert "item-002" in lazy_stack

    # Test timestamp grouping
    timestamps = lazy_stack.timestamps()
    assert len(timestamps) == 2  # Each item has its own timestamp
    assert isinstance(timestamps[0], datetime)
    assert timestamps[0].year == 2021
    assert timestamps[0].month == 1
    assert timestamps[0].day == 1

    assert isinstance(lazy_stack.get_timestamp("item-001"), datetime)
    assert isinstance(lazy_stack.get_timestamp("item-002"), datetime)


def test_lazy_raster_stack_with_key_fn_only():
    """Test RasterStack with only key_fn (no timestamp_fn)."""
    # Create a mock asset
    mock_asset = {"id": "item-001", "properties": {"datetime": "2021-01-01T00:00:00Z"}}

    # Create a list of tasks
    tasks = [(mock_task, mock_asset)]

    # Create a RasterStack using new API with only key_fn
    lazy_stack = RasterStack(
        tasks=tasks,
        key_fn=lambda asset: asset["properties"]["datetime"],
    )

    assert len(lazy_stack) > 0
    # Check that no tasks have been executed yet (cache is empty)
    assert len(lazy_stack._data_cache) == 0

    # Should be accessible via the datetime string key
    assert "2021-01-01T00:00:00Z" in lazy_stack

    # Accessing should work and trigger lazy execution
    image = lazy_stack["2021-01-01T00:00:00Z"]
    assert isinstance(image, ImageData)
    # Check that only the requested task has been executed (cache has one item)
    assert len(lazy_stack._data_cache) == 1
    assert "2021-01-01T00:00:00Z" in lazy_stack._data_cache


def test_lazy_raster_stack_temporal_ordering():
    """Test that RasterStack returns data in temporal order."""
    import datetime

    # Test data with timestamps in mixed order
    assets = [
        {
            "id": "item_2",  # middle timestamp
            "datetime": datetime.datetime(2023, 6, 15),
        },
        {
            "id": "item_1",  # earliest timestamp
            "datetime": datetime.datetime(2023, 6, 10),
        },
        {
            "id": "item_3",  # latest timestamp
            "datetime": datetime.datetime(2023, 6, 20),
        },
    ]

    # Create tasks using the same pattern as other tests
    tasks = [(mock_task, asset) for asset in assets]

    lazy_stack = RasterStack(
        tasks=tasks,
        key_fn=lambda asset: asset["id"],
        timestamp_fn=lambda asset: asset["datetime"],
    )

    # Keys should be in temporal order, not insertion order
    keys = list(lazy_stack.keys())
    assert keys == ["item_1", "item_2", "item_3"]

    # Timestamp mapping should work
    assert lazy_stack._timestamp_map["item_1"] == datetime.datetime(2023, 6, 10)
    assert lazy_stack._timestamp_map["item_2"] == datetime.datetime(2023, 6, 15)
    assert lazy_stack._timestamp_map["item_3"] == datetime.datetime(2023, 6, 20)

    # Iteration methods should maintain temporal order
    assert list(lazy_stack.keys()) == ["item_1", "item_2", "item_3"]

    # Test that values() and items() preserve temporal ordering
    # (Note: we can't test actual values without executing the tasks due to HTTP requests)


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

    # Create multiple mock assets
    assets = [
        {"id": "item-001", "properties": {"datetime": "2021-01-01T00:00:00Z"}},
        {"id": "item-002", "properties": {"datetime": "2021-01-02T00:00:00Z"}},
        {"id": "item-003", "properties": {"datetime": "2021-01-03T00:00:00Z"}},
    ]

    # Create tasks
    tasks = [(counting_mock_task, asset) for asset in assets]

    # Create RasterStack
    lazy_stack = RasterStack(
        tasks=tasks,
        key_fn=lambda asset: asset["id"],
        timestamp_fn=lambda asset: datetime.fromisoformat(
            asset["properties"]["datetime"].replace("Z", "+00:00")
        ),
    )

    # Initially, no tasks should be executed
    assert execution_counter["count"] == 0
    assert len(lazy_stack._data_cache) == 0

    # Check that we can inspect the stack without executing tasks
    assert len(lazy_stack) == 3
    assert "item-001" in lazy_stack
    assert list(lazy_stack.keys()) == ["item-001", "item-002", "item-003"]
    assert execution_counter["count"] == 0  # Still no execution

    # Access one item - should execute only that task
    item1 = lazy_stack["item-001"]
    assert execution_counter["count"] == 1  # Only one task executed
    assert len(lazy_stack._data_cache) == 1
    assert "item-001" in lazy_stack._data_cache
    assert isinstance(item1, ImageData)

    # Access the same item again - should not re-execute (cached)
    item1_again = lazy_stack["item-001"]
    assert execution_counter["count"] == 1  # Still only one execution
    assert item1 is item1_again  # Same object from cache

    # Access a different item - should execute only that task
    _ = lazy_stack["item-002"]
    assert execution_counter["count"] == 2  # Now two tasks executed
    assert len(lazy_stack._data_cache) == 2
    assert "item-002" in lazy_stack._data_cache

    # Access item-003 - should execute only that task
    _ = lazy_stack["item-003"]
    assert execution_counter["count"] == 3  # Now all three tasks executed

    # Test that all items are now cached
    assert len(lazy_stack._data_cache) == 3


def test_lazy_raster_stack_first_last_properties():
    """Test the first and last properties for efficient RasterStack access."""
    # Test with RasterStack
    assets = [
        {"id": "item-001", "properties": {"datetime": "2021-01-01T00:00:00Z"}},
        {"id": "item-002", "properties": {"datetime": "2021-01-02T00:00:00Z"}},
        {"id": "item-003", "properties": {"datetime": "2021-01-03T00:00:00Z"}},
    ]

    tasks = [(mock_task, asset) for asset in assets]

    lazy_stack = RasterStack(
        tasks=tasks,
        key_fn=lambda asset: asset["id"],
        timestamp_fn=lambda asset: datetime.fromisoformat(
            asset["properties"]["datetime"].replace("Z", "+00:00")
        ),
    )

    # Test first property (should only execute first task)
    first_item = lazy_stack.first
    assert isinstance(first_item, ImageData)
    assert len(lazy_stack._data_cache) == 1
    assert "item-001" in lazy_stack._data_cache

    # Test last property (should execute last task)
    last_item = lazy_stack.last
    assert isinstance(last_item, ImageData)
    assert len(lazy_stack._data_cache) == 2  # First and last now cached
    assert "item-003" in lazy_stack._data_cache

    # Test from_images factory method
    single_img = mock_task()
    stack_from_img = RasterStack.from_images({"data": single_img})
    assert isinstance(stack_from_img, RasterStack)
    assert "data" in stack_from_img
    assert stack_from_img["data"].array.shape == single_img.array.shape


def test_lazy_raster_stack_error_handling():
    """Test error handling in RasterStack."""
    from rio_tiler.errors import TileOutsideBounds

    # Create a task that will fail
    def failing_task():
        raise TileOutsideBounds("Test error")

    # Create a task that will succeed
    def success_task():
        return mock_task()

    assets = [
        {"id": "failing-item", "properties": {"datetime": "2021-01-01T00:00:00Z"}},
        {"id": "success-item", "properties": {"datetime": "2021-01-02T00:00:00Z"}},
    ]

    tasks = [(failing_task, assets[0]), (success_task, assets[1])]

    lazy_stack = RasterStack(
        tasks=tasks,
        key_fn=lambda asset: asset["id"],
        timestamp_fn=lambda asset: datetime.fromisoformat(
            asset["properties"]["datetime"].replace("Z", "+00:00")
        ),
        allowed_exceptions=(TileOutsideBounds,),
    )

    # Test that accessing a failing task raises KeyError
    with pytest.raises(KeyError, match="Task execution failed"):
        _ = lazy_stack["failing-item"]

    # Test that successful task still works
    success_img = lazy_stack["success-item"]
    assert isinstance(success_img, ImageData)

    # Test accessing non-existent key
    with pytest.raises(KeyError, match="not found in RasterStack"):
        _ = lazy_stack["non-existent-key"]

    # Test get method with non-existent key
    assert lazy_stack.get("non-existent-key") is None
    assert lazy_stack.get("non-existent-key", "default") == "default"


def test_lazy_raster_stack_selective_execution():
    """Test selective task execution methods."""
    execution_order = []

    def tracking_task(task_id):
        def task():
            execution_order.append(task_id)
            return mock_task()

        return task

    assets = [
        {"id": "item-001", "properties": {"datetime": "2021-01-01T00:00:00Z"}},
        {"id": "item-002", "properties": {"datetime": "2021-01-02T00:00:00Z"}},
        {"id": "item-003", "properties": {"datetime": "2021-01-03T00:00:00Z"}},
        {"id": "item-004", "properties": {"datetime": "2021-01-04T00:00:00Z"}},
    ]

    tasks = [(tracking_task(asset["id"]), asset) for asset in assets]

    lazy_stack = RasterStack(
        tasks=tasks,
        key_fn=lambda asset: asset["id"],
        timestamp_fn=lambda asset: datetime.fromisoformat(
            asset["properties"]["datetime"].replace("Z", "+00:00")
        ),
    )

    # Test selective execution by direct access
    _ = lazy_stack["item-001"]
    assert execution_order == ["item-001"]

    _ = lazy_stack["item-003"]
    assert execution_order == ["item-001", "item-003"]

    # Access remaining items via values()
    _ = lazy_stack.values()
    assert len(execution_order) == 4
    assert set(execution_order) == {"item-001", "item-002", "item-003", "item-004"}


def test_lazy_raster_stack_timestamps():
    """Test timestamp-related functionality."""
    assets = [
        {
            "id": "item-001",
            "properties": {"datetime": "2021-01-03T00:00:00Z"},
        },  # Latest
        {
            "id": "item-002",
            "properties": {"datetime": "2021-01-01T00:00:00Z"},
        },  # Earliest
        {
            "id": "item-003",
            "properties": {"datetime": "2021-01-02T00:00:00Z"},
        },  # Middle
    ]

    tasks = [(mock_task, asset) for asset in assets]

    lazy_stack = RasterStack(
        tasks=tasks,
        key_fn=lambda asset: asset["id"],
        timestamp_fn=lambda asset: datetime.fromisoformat(
            asset["properties"]["datetime"].replace("Z", "+00:00")
        ),
    )

    # Test temporal ordering (keys should be sorted by timestamp)
    keys = list(lazy_stack.keys())
    assert keys == ["item-002", "item-003", "item-001"]  # Chronological order

    # Test timestamps method
    timestamps = lazy_stack.timestamps()
    assert len(timestamps) == 3
    assert timestamps[0] == datetime.fromisoformat("2021-01-01T00:00:00+00:00")
    assert timestamps[1] == datetime.fromisoformat("2021-01-02T00:00:00+00:00")
    assert timestamps[2] == datetime.fromisoformat("2021-01-03T00:00:00+00:00")

    # Test get_timestamp
    assert lazy_stack.get_timestamp("item-001") == datetime.fromisoformat(
        "2021-01-03T00:00:00+00:00"
    )
    assert lazy_stack.get_timestamp("item-002") == datetime.fromisoformat(
        "2021-01-01T00:00:00+00:00"
    )
    assert lazy_stack.get_timestamp("non-existent") is None


def test_lazy_raster_stack_without_timestamps():
    """Test RasterStack without timestamp function."""
    assets = [
        {"id": "item-001"},
        {"id": "item-002"},
        {"id": "item-003"},
    ]

    tasks = [(mock_task, asset) for asset in assets]

    lazy_stack = RasterStack(
        tasks=tasks,
        key_fn=lambda asset: asset["id"],
        # No timestamp_fn provided
    )

    # Test that basic functionality works
    assert len(lazy_stack) == 3
    assert "item-001" in lazy_stack

    # Test that timestamp methods return empty/None
    assert len(lazy_stack.timestamps()) == 0
    assert lazy_stack.get_timestamp("item-001") is None


def test_lazy_raster_stack_max_workers():
    """Test RasterStack with different max_workers settings."""
    assets = [
        {"id": "item-001"},
        {"id": "item-002"},
    ]

    tasks = [(mock_task, asset) for asset in assets]

    # Test with different max_workers values
    for max_workers in [1, 2, 4]:
        lazy_stack = RasterStack(
            tasks=tasks,
            key_fn=lambda asset: asset["id"],
            max_workers=max_workers,
        )

        assert lazy_stack._max_workers == max_workers

        # Test that it still works correctly
        item = lazy_stack["item-001"]
        assert isinstance(item, ImageData)


def test_lazy_raster_stack_future_tasks():
    """Test RasterStack with Future-based tasks."""
    from concurrent.futures import ThreadPoolExecutor

    def slow_task():
        return mock_task()

    assets = [
        {"id": "item-001"},
        {"id": "item-002"},
    ]

    # Create Future-based tasks
    tasks = []
    with ThreadPoolExecutor(max_workers=2) as executor:
        for asset in assets:
            future = executor.submit(slow_task)
            tasks.append((future, asset))

        lazy_stack = RasterStack(
            tasks=tasks,
            key_fn=lambda asset: asset["id"],
        )

        # Access items (should work with Future tasks)
        item1 = lazy_stack["item-001"]
        item2 = lazy_stack["item-002"]

        assert isinstance(item1, ImageData)
        assert isinstance(item2, ImageData)
        assert len(lazy_stack._data_cache) == 2


def test_lazy_raster_stack_iteration_methods():
    """Test iteration methods of RasterStack."""
    assets = [
        {"id": "item-001", "properties": {"datetime": "2021-01-01T00:00:00Z"}},
        {"id": "item-002", "properties": {"datetime": "2021-01-02T00:00:00Z"}},
    ]

    tasks = [(mock_task, asset) for asset in assets]

    lazy_stack = RasterStack(
        tasks=tasks,
        key_fn=lambda asset: asset["id"],
        timestamp_fn=lambda asset: datetime.fromisoformat(
            asset["properties"]["datetime"].replace("Z", "+00:00")
        ),
    )

    # Test iteration without executing tasks
    assert len(lazy_stack._data_cache) == 0

    # Test keys()
    keys = list(lazy_stack.keys())
    assert keys == ["item-001", "item-002"]
    assert len(lazy_stack._data_cache) == 0  # No tasks executed

    # Test __iter__
    iter_keys = list(lazy_stack)
    assert iter_keys == ["item-001", "item-002"]
    assert len(lazy_stack._data_cache) == 0  # No tasks executed

    # Test __contains__
    assert "item-001" in lazy_stack
    assert "item-999" not in lazy_stack
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
    assert all(k in ["item-001", "item-002"] for k, v in items)
    assert len(lazy_stack._data_cache) == 2  # No new executions


def test_lazy_raster_stack_execute_all_tasks():
    """Test the _execute_all_tasks method for backward compatibility."""
    execution_count = {"count": 0}

    def counting_task():
        execution_count["count"] += 1
        return mock_task()

    assets = [
        {"id": "item-001"},
        {"id": "item-002"},
        {"id": "item-003"},
    ]

    tasks = [(counting_task, asset) for asset in assets]

    lazy_stack = RasterStack(
        tasks=tasks,
        key_fn=lambda asset: asset["id"],
    )

    # Initially no tasks executed
    assert execution_count["count"] == 0
    assert len(lazy_stack._data_cache) == 0

    # Call _execute_all_tasks directly
    lazy_stack._execute_all_tasks()

    # All tasks should now be executed
    assert execution_count["count"] == 3
    assert len(lazy_stack._data_cache) == 3
    assert all(
        key in lazy_stack._data_cache for key in ["item-001", "item-002", "item-003"]
    )


def test_lazy_raster_stack_edge_cases():
    """Test various edge cases and boundary conditions."""

    # Test empty task list
    empty_lazy_stack = RasterStack(
        tasks=[],
        key_fn=lambda asset: asset["id"],
    )

    assert len(empty_lazy_stack) == 0
    assert list(empty_lazy_stack.keys()) == []
    assert list(empty_lazy_stack.values()) == []
    assert list(empty_lazy_stack.items()) == []
    assert len(empty_lazy_stack.timestamps()) == 0

    # Test with duplicate keys (should handle gracefully)
    assets = [
        {"id": "duplicate-key"},
        {"id": "duplicate-key"},  # Same key
        {"id": "unique-key"},
    ]

    tasks = [(mock_task, asset) for asset in assets]

    # This should work but keys list will have duplicates
    lazy_stack_with_dupes = RasterStack(
        tasks=tasks,
        key_fn=lambda asset: asset["id"],
    )

    # Should still create the structure even with duplicate keys
    assert len(lazy_stack_with_dupes._keys) == 3
    assert "duplicate-key" in lazy_stack_with_dupes._keys
    assert "unique-key" in lazy_stack_with_dupes._keys


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

    # Create tasks where first 2 fail and 3rd succeeds
    assets = [
        {"id": "fail1", "datetime": "2021-01-01T00:00:00Z"},
        {"id": "fail2", "datetime": "2021-01-02T00:00:00Z"},
        {"id": "success", "datetime": "2021-01-03T00:00:00Z"},
    ]

    tasks = [
        (create_failing_task(), assets[0]),
        (create_failing_task(), assets[1]),
        (create_successful_task(42), assets[2]),
    ]

    lazy_stack = RasterStack(
        tasks=tasks,
        key_fn=lambda asset: asset["id"],
        timestamp_fn=lambda asset: datetime.fromisoformat(
            asset["datetime"].replace("Z", "+00:00")
        ),
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

    assets = [{"id": "fail1"}, {"id": "fail2"}]
    tasks = [(create_failing_task(), asset) for asset in assets]

    lazy_stack = RasterStack(
        tasks=tasks,
        key_fn=lambda asset: asset["id"],
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
    dates = ["2023-01-05", "2023-01-03", "2023-01-01", "2023-01-04", "2023-01-02"]
    tasks = []

    for i, date in enumerate(dates):
        asset = {"id": f"item_{i}", "datetime": f"{date}T00:00:00Z"}
        task = create_task_with_value(i * 10)  # Values: 0, 10, 20, 30, 40
        tasks.append((task, asset))

    lazy_stack = RasterStack(
        tasks=tasks,
        key_fn=lambda asset: asset["id"],
        timestamp_fn=lambda asset: datetime.fromisoformat(
            asset["datetime"].replace("Z", "+00:00")
        ),
    )

    # Keys should be in temporal order (chronological)
    expected_temporal_order = ["item_2", "item_4", "item_1", "item_3", "item_0"]
    assert list(lazy_stack.keys()) == expected_temporal_order

    # Key-to-task mapping should preserve original indices
    assert lazy_stack._key_to_task_index["item_0"] == 0  # First task
    assert lazy_stack._key_to_task_index["item_1"] == 1  # Second task
    assert lazy_stack._key_to_task_index["item_2"] == 2  # Third task
    assert lazy_stack._key_to_task_index["item_3"] == 3  # Fourth task
    assert lazy_stack._key_to_task_index["item_4"] == 4  # Fifth task

    # Each key should return the correct value from its original task
    assert lazy_stack["item_0"].array[0, 0, 0] == 0  # Task 0 value
    assert lazy_stack["item_1"].array[0, 0, 0] == 10  # Task 1 value
    assert lazy_stack["item_2"].array[0, 0, 0] == 20  # Task 2 value
    assert lazy_stack["item_3"].array[0, 0, 0] == 30  # Task 3 value
    assert lazy_stack["item_4"].array[0, 0, 0] == 40  # Task 4 value


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

    # Create tasks where first 2 fail and 3rd succeeds
    assets = [
        {"id": "fail1", "datetime": "2021-01-01T00:00:00Z"},
        {"id": "fail2", "datetime": "2021-01-02T00:00:00Z"},
        {"id": "success", "datetime": "2021-01-03T00:00:00Z"},
    ]

    tasks = [
        (create_failing_task(), assets[0]),
        (create_failing_task(), assets[1]),
        (create_successful_task(42), assets[2]),
    ]

    lazy_stack = RasterStack(
        tasks=tasks,
        key_fn=lambda asset: asset["id"],
        timestamp_fn=lambda asset: datetime.fromisoformat(
            asset["datetime"].replace("Z", "+00:00")
        ),
        allowed_exceptions=(TileOutsideBounds,),
    )

    # apply_pixel_selection should process available images for pixel selection
    # It will skip failed tasks and process successful ones
    result = apply_pixel_selection(lazy_stack, pixel_selection="first")

    assert isinstance(result, dict)
    assert "data" in result
    assert isinstance(result["data"], ImageData)
    # The result should contain pixel selection from successful images
    assert result["data"].array[0, 0, 0] == 42  # Value from the successful task


def test_empty_lazy_raster_stack():
    """Test RasterStack with empty tasks list."""
    # Create empty RasterStack
    lazy_stack = RasterStack(
        tasks=[],
        key_fn=lambda asset: asset["id"],
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
