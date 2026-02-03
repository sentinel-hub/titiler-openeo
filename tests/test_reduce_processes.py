"""Tests for reduction process implementations."""

from datetime import datetime

import numpy as np
import pytest
from rio_tiler.models import ImageData

from titiler.openeo.processes.implementations.data_model import RasterStack
from titiler.openeo.processes.implementations.reduce import (
    DimensionNotAvailable,
    apply_pixel_selection,
    reduce_dimension,
)


@pytest.fixture
def sample_temporal_stack():
    """Create a sample RasterStack with temporal dimension for testing."""
    # Create multiple dates of single-band data
    images = {}
    for i in range(3):
        date = datetime(2021, 1, i + 1)
        data = np.ma.array(
            np.ones((1, 10, 10), dtype=np.float32)
            * (i + 1),  # Each date has different value
            mask=np.zeros((1, 10, 10), dtype=bool),
        )
        images[date] = ImageData(data, band_names=["band1"])

    return RasterStack.from_images(images)


@pytest.fixture
def sample_spectral_stack():
    """Create a sample RasterStack with a single date but multiple bands."""
    # Create a multi-band image
    data = np.ma.array(
        np.array(
            [
                np.ones((10, 10), dtype=np.float32),  # Band 1: all 1s
                np.ones((10, 10), dtype=np.float32) * 2,  # Band 2: all 2s
                np.ones((10, 10), dtype=np.float32) * 3,  # Band 3: all 3s
            ]
        ),
        mask=np.zeros((3, 10, 10), dtype=bool),
    )

    return RasterStack.from_images(
        {datetime(2021, 1, 1): ImageData(data, band_names=["red", "green", "blue"])}
    )


def test_reduce_temporal_dimension(sample_temporal_stack):
    """Test reducing the temporal dimension of a RasterStack."""

    # Mean reducer for temporal dimension
    def mean_reducer(data):
        """Calculate mean across temporal dimension."""
        # Extract arrays and stack them
        stacked = np.stack([img.array for key, img in data.items()])
        # Calculate mean along first axis (temporal)
        mean = np.mean(stacked, axis=0)
        return mean

    # Reduce temporal dimension
    result = reduce_dimension(
        data=sample_temporal_stack, reducer=mean_reducer, dimension="temporal"
    )

    # Result should be a RasterStack with 1 ImageData
    assert isinstance(result, RasterStack)
    assert len(result) == 1
    assert result.first is not None
    img = result.first
    assert img.count == 1
    assert img.array.shape == (1, 10, 10)  # Single band with original shape
    assert img.band_names == ["band1"]
    assert img.metadata["reduced_dimension"] == "temporal"
    assert img.metadata["reduction_method"] == "mean_reducer"
    # Check the mean value is approximately 2.0
    assert np.allclose(img.array.mean(), 2.0)


def test_reduce_spectral_dimension(sample_spectral_stack):
    """Test reducing the spectral dimension of a RasterStack."""

    # Mean reducer for spectral dimension
    def mean_reducer(data, **kwargs):
        """Calculate mean across spectral dimension."""
        # After our changes, data is now the array directly, not ImageData
        if hasattr(data, "array"):
            # Old format - ImageData object
            return np.mean(data.array, axis=0)
        else:
            # New format - array directly
            return np.mean(data, axis=0)

    # Reduce spectral dimension
    result = reduce_dimension(
        data=sample_spectral_stack, reducer=mean_reducer, dimension="spectral"
    )

    # Result should still be a RasterStack but with single-band images
    assert isinstance(result, RasterStack)
    assert len(result) == 1
    # Keys are now datetime objects
    assert result.first is not None

    # Check that each image now has 1 band with mean value of (1+2+3)/3 = 2
    img = result.first
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
            data=sample_temporal_stack, reducer=identity, dimension="invalid_dimension"
        )


def test_apply_pixel_selection(sample_temporal_stack):
    """Test the apply_pixel_selection function."""
    # Apply pixel selection to combine temporal stack
    result = apply_pixel_selection(data=sample_temporal_stack, pixel_selection="mean")

    # Result should be a RasterStack with a single ImageData
    assert isinstance(result, RasterStack)
    assert len(result) == 1
    assert result.first is not None
    img = result.first
    assert isinstance(img, ImageData)
    assert img.count == 1

    # The mean value should be (1+2+3)/3 = 2
    assert np.allclose(img.array.mean(), 2.0)

    # Metadata should include the pixel selection method
    assert "pixel_selection_method" in img.metadata
    assert img.metadata["pixel_selection_method"] == "mean"
