"""Test LazyRasterStack with apply_pixel_selection."""

import numpy as np
from datetime import datetime
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
        timestamp_fn=lambda asset: datetime.fromisoformat(asset["properties"]["datetime"].replace('Z', '+00:00')),
    )

    assert len(lazy_stack) > 0
    assert lazy_stack._executed is False

    # Apply pixel selection
    result = apply_pixel_selection(lazy_stack, pixel_selection="first")

    assert isinstance(result, dict)  # RasterStack is Dict[str, ImageData]
    assert "data" in result
    assert isinstance(result["data"], ImageData)
    assert lazy_stack._executed is True


def test_lazy_raster_stack_duplicate_timestamps():
    """Test that LazyRasterStack handles multiple items with the same timestamp correctly."""
    # Create mock assets with same datetime but different IDs
    mock_asset_1 = {"id": "item-001", "properties": {"datetime": "2021-01-01T00:00:00Z"}}
    mock_asset_2 = {"id": "item-002", "properties": {"datetime": "2021-01-01T00:00:00Z"}}

    # Create tasks
    tasks = [(mock_task, mock_asset_1), (mock_task, mock_asset_2)]

    # Create a LazyRasterStack
    lazy_stack = LazyRasterStack(
        tasks=tasks,
        key_fn=lambda asset: asset["id"],
        timestamp_fn=lambda asset: datetime.fromisoformat(asset["properties"]["datetime"].replace('Z', '+00:00')),
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


def test_lazy_raster_stack_backward_compatibility():
    """Test backward compatibility with old date_name_fn parameter."""
    # Create a mock asset
    mock_asset = {"id": "item-001", "properties": {"datetime": "2021-01-01T00:00:00Z"}}

    # Create a list of tasks
    tasks = [(mock_task, mock_asset)]

    # Create a LazyRasterStack using old API
    lazy_stack = LazyRasterStack(
        tasks=tasks,
        date_name_fn=lambda asset: asset["properties"]["datetime"],
    )

    assert len(lazy_stack) > 0
    assert lazy_stack._executed is False

    # Should be accessible via the datetime string key (old behavior)
    assert "2021-01-01T00:00:00Z" in lazy_stack
    
    # Accessing should work
    image = lazy_stack["2021-01-01T00:00:00Z"]
    assert isinstance(image, ImageData)
    assert lazy_stack._executed is True
