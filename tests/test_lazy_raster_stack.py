"""Test LazyRasterStack with apply_pixel_selection."""

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
    mock_asset = {"properties": {"datetime": "2021-01-01"}}

    # Create a list of tasks
    tasks = [(mock_task, mock_asset)]

    # Create a LazyRasterStack
    lazy_stack = LazyRasterStack(
        tasks=tasks,
        date_name_fn=lambda asset: asset["properties"]["datetime"],
    )

    assert len(lazy_stack) > 0
    assert lazy_stack._executed is False

    # Apply pixel selection
    result = apply_pixel_selection(lazy_stack, pixel_selection="first")

    assert type(result) is ImageData
    assert lazy_stack._executed is True
