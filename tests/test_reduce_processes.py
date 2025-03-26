"""Tests for reduction process implementations."""

import pytest
import numpy as np
from rio_tiler.models import ImageData

from titiler.openeo.processes.implementations.reduce import (
    reduce_dimension, apply_pixel_selection, DimensionNotAvailable
)


@pytest.fixture
def sample_temporal_stack():
    """Create a sample RasterStack with temporal dimension for testing."""
    # Create multiple dates of single-band data
    stack = {}
    for i, date in enumerate(["2021-01-01", "2021-01-02", "2021-01-03"]):
        data = np.ma.array(
            np.ones((1, 10, 10), dtype=np.float32) * (i + 1),  # Each date has different value
            mask=np.zeros((1, 10, 10), dtype=bool)
        )
        stack[date] = ImageData(data, band_names=["band1"])
    
    return stack


@pytest.fixture
def sample_spectral_stack():
    """Create a sample RasterStack with a single date but multiple bands."""
    # Create a multi-band image
    data = np.ma.array(
        np.array([
            np.ones((10, 10), dtype=np.float32),         # Band 1: all 1s
            np.ones((10, 10), dtype=np.float32) * 2,     # Band 2: all 2s
            np.ones((10, 10), dtype=np.float32) * 3      # Band 3: all 3s
        ]),
        mask=np.zeros((3, 10, 10), dtype=bool)
    )
    
    return {
        "2021-01-01": ImageData(data, band_names=["red", "green", "blue"])
    }


def test_reduce_temporal_dimension(sample_temporal_stack):
    """Test reducing the temporal dimension of a RasterStack."""
    # Mean reducer for temporal dimension
    def mean_reducer(arrays, context=None, **kwargs):
        """Calculate mean across temporal dimension."""
        # Extract arrays and stack them
        stacked = np.stack([item["data"] for item in arrays])
        # Calculate mean along first axis (temporal)
        return np.mean(stacked, axis=0)
    
    # Reduce temporal dimension
    result = reduce_dimension(
        data=sample_temporal_stack, 
        reducer=mean_reducer, 
        dimension="temporal"
    )
    
    # Result should be a single ImageData with values = mean(1,2,3) = 2
    assert isinstance(result, ImageData)
    assert result.count == 1
    # Check the mean value is approximately 2.0
    assert np.allclose(result.array.mean(), 2.0)


def test_reduce_spectral_dimension(sample_spectral_stack):
    """Test reducing the spectral dimension of a RasterStack."""
    # Mean reducer for spectral dimension
    def mean_reducer(data, **kwargs):
        """Calculate mean across spectral dimension."""
        if isinstance(data, ImageData):
            # Mean across bands (first dimension)
            return np.mean(data.array, axis=0)
        return None
    
    # Reduce spectral dimension
    result = reduce_dimension(
        data=sample_spectral_stack, 
        reducer=mean_reducer, 
        dimension="spectral"
    )
    
    # Result should still be a RasterStack but with single-band images
    assert isinstance(result, dict)
    assert len(result) == 1
    assert "2021-01-01" in result
    
    # Check that each image now has 1 band with mean value of (1+2+3)/3 = 2
    img = result["2021-01-01"]
    assert img.count == 1
    assert np.allclose(img.array.mean(), 2.0)


def test_reduce_invalid_dimension(sample_temporal_stack):
    """Test reducing an invalid dimension raises an exception."""
    # Identity reducer
    def identity(data, **kwargs):
        return data
    
    # Try to reduce a non-existent dimension
    with pytest.raises(DimensionNotAvailable):
        reduce_dimension(
            data=sample_temporal_stack, 
            reducer=identity, 
            dimension="invalid_dimension"
        )


def test_apply_pixel_selection(sample_temporal_stack):
    """Test the apply_pixel_selection function."""
    # Apply pixel selection to combine temporal stack
    result = apply_pixel_selection(
        data=sample_temporal_stack,
        pixel_selection="mean"
    )
    
    # Result should be a single ImageData
    assert isinstance(result, ImageData)
    assert result.count == 1
    
    # The mean value should be (1+2+3)/3 = 2
    assert np.allclose(result.array.mean(), 2.0)
    
    # Metadata should include the pixel selection method
    assert "pixel_selection_method" in result.metadata
    assert result.metadata["pixel_selection_method"] == "mean"


def test_no_temporal_dimension(sample_spectral_stack):
    """Test reducing temporal dimension when it doesn't exist."""
    # Mean reducer
    def mean_reducer(arrays, **kwargs):
        return np.mean(np.stack([item["data"] for item in arrays]), axis=0)
    
    # Reduce temporal dimension of a single-date stack
    with pytest.raises(DimensionNotAvailable):
        reduce_dimension(
            data=sample_spectral_stack, 
            reducer=mean_reducer, 
            dimension="temporal"
        )
