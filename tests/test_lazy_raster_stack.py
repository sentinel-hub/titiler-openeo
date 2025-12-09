"""Test LazyRasterStack with apply_pixel_selection."""

from datetime import datetime

import numpy as np
import pytest
from rio_tiler.models import ImageData

from titiler.openeo.processes.implementations.data_model import LazyRasterStack
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

    # Create a LazyRasterStack
    lazy_stack = LazyRasterStack(
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


def test_lazy_raster_stack_duplicate_timestamps():
    """Test that LazyRasterStack handles multiple items with the same timestamp correctly."""
    # Create mock assets with same datetime but different IDs
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

    # Create a LazyRasterStack
    lazy_stack = LazyRasterStack(
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
    assert len(timestamps) == 1
    assert isinstance(timestamps[0], datetime)
    assert timestamps[0].year == 2021
    assert timestamps[0].month == 1
    assert timestamps[0].day == 1

    assert isinstance(lazy_stack.get_timestamp("item-001"), datetime)
    assert isinstance(lazy_stack.get_timestamp("item-002"), datetime)

    # Test getting by timestamp - use the actual timestamp from the stack
    test_dt = timestamps[0]
    items_by_timestamp = lazy_stack.get_by_timestamp(test_dt)
    assert len(items_by_timestamp) == 2
    assert "item-001" in items_by_timestamp
    assert "item-002" in items_by_timestamp

    # Test groupby timestamp
    grouped = lazy_stack.groupby_timestamp()
    assert test_dt in grouped
    assert len(grouped[test_dt]) == 2


def test_lazy_raster_stack_with_key_fn_only():
    """Test LazyRasterStack with only key_fn (no timestamp_fn)."""
    # Create a mock asset
    mock_asset = {"id": "item-001", "properties": {"datetime": "2021-01-01T00:00:00Z"}}

    # Create a list of tasks
    tasks = [(mock_task, mock_asset)]

    # Create a LazyRasterStack using new API with only key_fn
    lazy_stack = LazyRasterStack(
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
    """Test that LazyRasterStack returns data in temporal order."""
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

    lazy_stack = LazyRasterStack(
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
    """Test that LazyRasterStack only executes tasks when specifically requested."""

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

    # Create LazyRasterStack
    lazy_stack = LazyRasterStack(
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

    # Test timestamp-based access - should execute only remaining task
    test_dt = datetime.fromisoformat("2021-01-03T00:00:00+00:00")
    items_by_timestamp = lazy_stack.get_by_timestamp(test_dt)
    assert execution_counter["count"] == 3  # Now all three tasks executed
    assert len(items_by_timestamp) == 1
    assert "item-003" in items_by_timestamp

    # Test that all items are now cached
    assert len(lazy_stack._data_cache) == 3


def test_lazy_raster_stack_utility_functions():
    """Test the utility functions for efficient RasterStack access."""
    from titiler.openeo.processes.implementations.data_model import (
        get_first_item,
        get_last_item,
        to_raster_stack,
    )

    # Test with single ImageData
    single_img = mock_task()

    # Test get_first_item with ImageData
    assert get_first_item(single_img) is single_img

    # Test get_last_item with ImageData
    assert get_last_item(single_img) is single_img

    # Test to_raster_stack with ImageData
    stack_from_img = to_raster_stack(single_img)
    assert isinstance(stack_from_img, dict)
    assert "data" in stack_from_img
    assert stack_from_img["data"] is single_img

    # Test with LazyRasterStack
    assets = [
        {"id": "item-001", "properties": {"datetime": "2021-01-01T00:00:00Z"}},
        {"id": "item-002", "properties": {"datetime": "2021-01-02T00:00:00Z"}},
        {"id": "item-003", "properties": {"datetime": "2021-01-03T00:00:00Z"}},
    ]

    tasks = [(mock_task, asset) for asset in assets]

    lazy_stack = LazyRasterStack(
        tasks=tasks,
        key_fn=lambda asset: asset["id"],
        timestamp_fn=lambda asset: datetime.fromisoformat(
            asset["properties"]["datetime"].replace("Z", "+00:00")
        ),
    )

    # Test get_first_item (should only execute first task)
    first_item = get_first_item(lazy_stack)
    assert isinstance(first_item, ImageData)
    assert len(lazy_stack._data_cache) == 1
    assert "item-001" in lazy_stack._data_cache

    # Test get_last_item (should execute last task)
    last_item = get_last_item(lazy_stack)
    assert isinstance(last_item, ImageData)
    assert len(lazy_stack._data_cache) == 2  # First and last now cached
    assert "item-003" in lazy_stack._data_cache

    # Test with regular dict RasterStack
    regular_stack = {
        "img1": mock_task(),
        "img2": mock_task(),
        "img3": mock_task(),
    }

    first_regular = get_first_item(regular_stack)
    last_regular = get_last_item(regular_stack)

    assert isinstance(first_regular, ImageData)
    assert isinstance(last_regular, ImageData)

    # Test error cases
    with pytest.raises(ValueError, match="Unsupported data type"):
        get_first_item("invalid_type")

    with pytest.raises(ValueError, match="Unsupported data type"):
        get_last_item(123)


def test_lazy_raster_stack_error_handling():
    """Test error handling in LazyRasterStack."""
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

    lazy_stack = LazyRasterStack(
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
    with pytest.raises(KeyError, match="not found in LazyRasterStack"):
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
        {
            "id": "item-002",
            "properties": {"datetime": "2021-01-01T00:00:00Z"},
        },  # Same timestamp
        {"id": "item-003", "properties": {"datetime": "2021-01-02T00:00:00Z"}},
        {
            "id": "item-004",
            "properties": {"datetime": "2021-01-02T00:00:00Z"},
        },  # Same timestamp
    ]

    tasks = [(tracking_task(asset["id"]), asset) for asset in assets]

    lazy_stack = LazyRasterStack(
        tasks=tasks,
        key_fn=lambda asset: asset["id"],
        timestamp_fn=lambda asset: datetime.fromisoformat(
            asset["properties"]["datetime"].replace("Z", "+00:00")
        ),
    )

    # Test selective execution by timestamp
    timestamp1 = datetime.fromisoformat("2021-01-01T00:00:00+00:00")
    items_t1 = lazy_stack.get_by_timestamp(timestamp1)

    assert len(items_t1) == 2
    assert "item-001" in items_t1
    assert "item-002" in items_t1

    # Only first timestamp tasks should be executed
    assert set(execution_order) == {"item-001", "item-002"}

    # Test groupby_timestamp
    grouped = lazy_stack.groupby_timestamp()

    # Should now have executed all tasks
    assert len(execution_order) == 4
    assert set(execution_order) == {"item-001", "item-002", "item-003", "item-004"}

    # Verify grouping
    assert len(grouped) == 2  # Two unique timestamps
    timestamp2 = datetime.fromisoformat("2021-01-02T00:00:00+00:00")
    assert timestamp1 in grouped
    assert timestamp2 in grouped
    assert len(grouped[timestamp1]) == 2
    assert len(grouped[timestamp2]) == 2


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

    lazy_stack = LazyRasterStack(
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

    # Test get_by_timestamp with non-existent timestamp
    future_timestamp = datetime.fromisoformat("2021-01-04T00:00:00+00:00")
    future_items = lazy_stack.get_by_timestamp(future_timestamp)
    assert len(future_items) == 0


def test_lazy_raster_stack_without_timestamps():
    """Test LazyRasterStack without timestamp function."""
    assets = [
        {"id": "item-001"},
        {"id": "item-002"},
        {"id": "item-003"},
    ]

    tasks = [(mock_task, asset) for asset in assets]

    lazy_stack = LazyRasterStack(
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

    # Test that get_by_timestamp returns empty for any timestamp
    any_timestamp = datetime.now()
    assert len(lazy_stack.get_by_timestamp(any_timestamp)) == 0

    # Test that groupby_timestamp returns empty
    grouped = lazy_stack.groupby_timestamp()
    assert len(grouped) == 0


def test_lazy_raster_stack_max_workers():
    """Test LazyRasterStack with different max_workers settings."""
    assets = [
        {"id": "item-001"},
        {"id": "item-002"},
    ]

    tasks = [(mock_task, asset) for asset in assets]

    # Test with different max_workers values
    for max_workers in [1, 2, 4]:
        lazy_stack = LazyRasterStack(
            tasks=tasks,
            key_fn=lambda asset: asset["id"],
            max_workers=max_workers,
        )

        assert lazy_stack._max_workers == max_workers

        # Test that it still works correctly
        item = lazy_stack["item-001"]
        assert isinstance(item, ImageData)


def test_lazy_raster_stack_future_tasks():
    """Test LazyRasterStack with Future-based tasks."""
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

        lazy_stack = LazyRasterStack(
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
    """Test iteration methods of LazyRasterStack."""
    assets = [
        {"id": "item-001", "properties": {"datetime": "2021-01-01T00:00:00Z"}},
        {"id": "item-002", "properties": {"datetime": "2021-01-02T00:00:00Z"}},
    ]

    tasks = [(mock_task, asset) for asset in assets]

    lazy_stack = LazyRasterStack(
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

    lazy_stack = LazyRasterStack(
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
    empty_lazy_stack = LazyRasterStack(
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
    lazy_stack_with_dupes = LazyRasterStack(
        tasks=tasks,
        key_fn=lambda asset: asset["id"],
    )

    # Should still create the structure even with duplicate keys
    assert len(lazy_stack_with_dupes._keys) == 3
    assert "duplicate-key" in lazy_stack_with_dupes._keys
    assert "unique-key" in lazy_stack_with_dupes._keys
