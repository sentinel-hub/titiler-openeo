"""Tests for I/O process implementations."""

import pytest
import numpy as np
from rio_tiler.models import ImageData

from titiler.openeo.processes.implementations.io import (
    save_result, SaveResultData
)


@pytest.fixture
def sample_image_data():
    """Create a sample ImageData object for testing."""
    # Create a 3-band image (e.g., RGB)
    data = np.ma.array(
        np.random.randint(0, 256, size=(3, 10, 10), dtype=np.uint8),
        mask=np.zeros((3, 10, 10), dtype=bool)
    )
    return ImageData(data, band_names=["red", "green", "blue"])


@pytest.fixture
def sample_raster_stack(sample_image_data):
    """Create a sample RasterStack (dict of ImageData) for testing."""
    # Create a second ImageData
    data2 = np.ma.array(
        np.random.randint(0, 256, size=(3, 10, 10), dtype=np.uint8),
        mask=np.zeros((3, 10, 10), dtype=bool)
    )
    image_data2 = ImageData(data2, band_names=["red", "green", "blue"])
    
    # Return a RasterStack with two samples
    return {
        "2021-01-01": sample_image_data,
        "2021-01-02": image_data2
    }


@pytest.fixture
def sample_feature_collection():
    """Create a sample GeoJSON FeatureCollection for testing."""
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[
                        [0, 0], [1, 0], [1, 1], [0, 1], [0, 0]
                    ]]
                },
                "properties": {
                    "name": "Test Feature",
                    "values": {
                        "2021-01-01": 123.45,
                        "2021-01-02": 678.90
                    }
                }
            }
        ]
    }


def test_save_result_png(sample_image_data):
    """Test saving a single ImageData as PNG."""
    # Create a single-entry RasterStack
    single_stack = {"2021-01-01": sample_image_data}
    
    # Save as PNG
    result = save_result(
        data=single_stack,
        format="png"
    )
    
    # For single images, should get a single SaveResultData
    assert isinstance(result, SaveResultData)
    assert result.media_type == "image/png"
    assert isinstance(result.data, bytes)


def test_save_result_numpy_array():
    """Test saving a numpy array directly."""
    # Create a simple numpy array
    array = np.random.randint(0, 256, size=(3, 10, 10), dtype=np.uint8)
    
    # Save as JPEG
    result = save_result(
        data=array,
        format="jpeg"
    )
    
    # Should get a single SaveResultData
    assert isinstance(result, SaveResultData)
    assert result.media_type == "image/jpeg"
    assert isinstance(result.data, bytes)


def test_save_result_single_image_stack(sample_raster_stack):
    """Test saving a RasterStack with a single image."""
    # Create a stack with just one image
    single_stack = {"2021-01-01": list(sample_raster_stack.values())[0]}
    
    # Save as PNG
    result = save_result(
        data=single_stack,
        format="png"
    )
    
    # For single-image stacks, should get a single SaveResultData
    assert isinstance(result, SaveResultData)
    assert result.media_type == "image/png"
    assert isinstance(result.data, bytes)


def test_save_result_geojson(sample_feature_collection):
    """Test saving a GeoJSON FeatureCollection."""
    # Save as JSON
    result = save_result(
        data=sample_feature_collection,
        format="json"
    )
    
    # Should get a single SaveResultData
    assert isinstance(result, SaveResultData)
    assert result.media_type == "application/json"
    assert isinstance(result.data, bytes)
    
    # Convert bytes back to string to verify it's valid JSON
    json_str = result.data.decode("utf-8")
    assert "FeatureCollection" in json_str  # Just check for the string presence
    assert "features" in json_str


# Skip this test for now as it requires GDAL drivers
# @pytest.mark.skip(reason="Requires proper GDAL driver setup")
def test_save_result_geotiff(sample_raster_stack):
    """Test saving a RasterStack as GeoTIFF."""
    # Add CRS and bounds to make it a valid GeoTIFF
    for key, img in sample_raster_stack.items():
        img.crs = "epsg:4326"
        img.bounds = (0, 0, 1, 1)
    
    # Save as GeoTIFF
    result = save_result(
        data=sample_raster_stack,
        format="gtiff"  # Use gtiff instead of tiff
    )
    
    # Should get a single SaveResultData with all bands
    assert isinstance(result, SaveResultData)
    assert result.media_type == "image/tiff"
    assert isinstance(result.data, bytes)
