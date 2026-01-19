"""Test script to verify type validation in process implementations."""

import numpy as np
import pytest
from rio_tiler.models import ImageData

from titiler.openeo.processes.implementations.arrays import array_create
from titiler.openeo.processes.implementations.data_model import RasterStack


def test_array_create_with_valid_array():
    """Test that array_create works with valid array input."""
    _ = array_create(data=[1, 2, 3], repeat=1)


def test_array_create_with_none():
    """Test that array_create works with None (default)."""
    _ = array_create(data=None, repeat=1)


def test_array_create_with_raster_stack():
    """Test that array_create rejects RasterStack input."""
    # Create a RasterStack
    raster_stack: RasterStack = {
        "band1": ImageData(
            np.array([[[1, 2], [3, 4]]]), bounds=(0, 0, 1, 1), crs="EPSG:4326"
        )
    }
    with pytest.raises(TypeError):
        _ = array_create(data=raster_stack, repeat=1)


def test_array_create_with_numpy_array():
    """Test that array_create works with numpy array."""
    _ = array_create(data=np.array([1, 2, 3]), repeat=1)
