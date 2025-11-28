#!/usr/bin/env python
"""Test script to verify type validation in process implementations."""

import numpy as np
from rio_tiler.models import ImageData

from titiler.openeo.processes.implementations.arrays import array_create
from titiler.openeo.processes.implementations.data_model import RasterStack


def test_array_create_with_valid_array():
    """Test that array_create works with valid array input."""
    print("Test 1: array_create with valid array input...")
    try:
        result = array_create(data=[1, 2, 3], repeat=1)
        print("✓ Success: array_create accepted array input")
        print(f"  Result: {result}")
    except TypeError as e:
        print(f"✗ Failed: {e}")
        return False
    return True


def test_array_create_with_none():
    """Test that array_create works with None (default)."""
    print("\nTest 2: array_create with None input...")
    try:
        result = array_create(data=None, repeat=1)
        print("✓ Success: array_create accepted None input")
        print(f"  Result shape: {result.shape}")
    except TypeError as e:
        print(f"✗ Failed: {e}")
        return False
    return True


def test_array_create_with_raster_stack():
    """Test that array_create rejects RasterStack input."""
    print("\nTest 3: array_create with RasterStack (should fail)...")

    # Create a RasterStack
    raster_stack: RasterStack = {
        "band1": ImageData(
            np.array([[[1, 2], [3, 4]]]), bounds=(0, 0, 1, 1), crs="EPSG:4326"
        )
    }

    try:
        result = array_create(data=raster_stack, repeat=1)
        print("✗ Failed: array_create should have rejected RasterStack but accepted it")
        print(f"  Result: {result}")
        return False
    except TypeError as e:
        print("✓ Success: array_create correctly rejected RasterStack")
        print(f"  Error message: {e}")
        return True


def test_array_create_with_numpy_array():
    """Test that array_create works with numpy array."""
    print("\nTest 4: array_create with numpy array...")
    try:
        result = array_create(data=np.array([1, 2, 3]), repeat=1)
        print("✓ Success: array_create accepted numpy array")
        print(f"  Result: {result}")
    except TypeError as e:
        print(f"✗ Failed: {e}")
        return False
    return True

