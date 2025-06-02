"""Test titiler.openeo.processes.implementations.io."""

import numpy
import pytest
from rio_tiler.models import ImageData

from titiler.openeo.processes.implementations.io import (
    SaveResultData,
    _handle_json_format,
    _handle_raster_geotiff,
    _handle_text_format,
    save_result,
)


def test_handle_text_format():
    """Test _handle_text_format function."""
    # Test with simple string
    result = _handle_text_format("test")
    assert isinstance(result, SaveResultData)
    assert result.media_type == "text/plain"
    assert result.data == b"test"

    # Test with dictionary
    data = {"key": "value", "num": 123}
    result = _handle_text_format(data)
    assert result.media_type == "text/plain"
    assert b"key" in result.data
    assert b"value" in result.data


def test_handle_json_format():
    """Test _handle_json_format function."""
    data = {"name": "test", "values": [1, 2, 3]}
    result = _handle_json_format(data, "json")
    assert isinstance(result, SaveResultData)
    assert result.media_type == "application/json"
    assert b'"name": "test"' in result.data
    assert b'"values": [1, 2, 3]' in result.data


def test_handle_raster_geotiff():
    """Test _handle_raster_geotiff function."""
    # Create test image data
    # Create 2x2 images, each with a single band
    array1 = numpy.array([[1, 2], [3, 4]], dtype=numpy.uint8)[None, ...]  # Shape: (1, 2, 2)
    array2 = numpy.array([[5, 6], [7, 8]], dtype=numpy.uint8)[None, ...]  # Shape: (1, 2, 2)
    
    data = {
        "band1": ImageData(array1),
        "band2": ImageData(array2),
    }

    result = _handle_raster_geotiff(data)
    assert isinstance(result, ImageData)
    assert result.array.shape == (2, 2, 2)  # 2 bands, 2x2 image
    assert (result.array[0] == array1[0]).all()
    assert (result.array[1] == array2[0]).all()
    assert result.band_names == ["band1", "band2"]


def test_handle_raster_geotiff_validation():
    """Test validation in _handle_raster_geotiff."""
    # Create 2x2 and 2x3 images for validation testing
    array1 = numpy.array([[1, 2], [3, 4]], dtype=numpy.uint8)[None, ...]  # Shape: (1, 2, 2)
    array2 = numpy.array([[5, 6, 7], [8, 9, 10]], dtype=numpy.uint8)[None, ...]  # Shape: (1, 2, 3)
    
    data = {
        "band1": ImageData(array1),
        "band2": ImageData(array2),
    }

    with pytest.raises(ValueError, match="same shape"):
        _handle_raster_geotiff(data)


def test_save_result_text():
    """Test save_result with text format."""
    data = "test data"
    result = save_result(data, "txt")
    assert isinstance(result, SaveResultData)
    assert result.media_type == "text/plain"
    assert result.data == b"test data"


def test_save_result_json():
    """Test save_result with JSON format."""
    data = {"test": "value"}
    result = save_result(data, "json")
    assert isinstance(result, SaveResultData)
    assert result.media_type == "application/json"
    assert b'"test": "value"' in result.data


def test_save_result_numpy():
    """Test save_result with numpy array."""
    data = numpy.array([[1, 2], [3, 4]], dtype=numpy.uint8)[None, ...]  # Shape: (1, 2, 2)
    result = save_result(data, "png")
    assert isinstance(result, SaveResultData)
    assert result.media_type == "image/png"
    assert len(result.data) > 0  # Should contain PNG bytes


def test_save_result_geotiff():
    """Test save_result with multi-band GeoTIFF."""
    # Create test data
    array1 = numpy.array([[1, 2], [3, 4]], dtype=numpy.byte)[None, ...]  # Shape: (1, 2, 2)
    array2 = numpy.array([[5, 6], [7, 8]], dtype=numpy.byte)[None, ...]  # Shape: (1, 2, 2)
    data = {
        "band1": ImageData(array1),
        "band2": ImageData(array2),
    }

    result = save_result(data, "gtiff")
    assert isinstance(result, SaveResultData)
    assert result.media_type == "image/tiff"
    assert len(result.data) > 0  # Should contain TIFF bytes


def test_save_result_single_image():
    """Test save_result with single image."""
    array = numpy.array([[1, 2], [3, 4]], dtype=numpy.uint8)[None, ...]  # Shape: (1, 2, 2)
    data = {"single": ImageData(array)}
    
    result = save_result(data, "png")
    assert isinstance(result, SaveResultData)
    assert result.media_type == "image/png"
    assert len(result.data) > 0  # Should contain PNG bytes


def test_save_result_feature_collection():
    """Test save_result with FeatureCollection."""
    data = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {
                    "values": {"2021-01-01": 1.0}
                }
            }
        ]
    }

    # Test JSON output
    json_result = save_result(data, "json")
    assert isinstance(json_result, SaveResultData)
    assert json_result.media_type == "application/json"
    assert b'"type": "FeatureCollection"' in json_result.data

    # Test CSV output
    csv_result = save_result(data, "csv")
    assert isinstance(csv_result, SaveResultData)
    assert csv_result.media_type == "text/csv"
    assert b"date,feature_index,value" in csv_result.data
    assert b"2021-01-01,0,1.0" in csv_result.data
