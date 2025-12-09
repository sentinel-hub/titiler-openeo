"""Test LazyRasterStack with apply_pixel_selection."""

from datetime import datetime

import numpy as np
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
