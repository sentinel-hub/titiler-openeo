"""Test titiler.openeo.processes.implementations.io."""

from datetime import datetime

import numpy
import pytest
from rio_tiler.models import ImageData

from titiler.openeo.processes.implementations.data_model import RasterStack
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
    array1 = numpy.array([[1, 2], [3, 4]], dtype=numpy.uint8)[
        None, ...
    ]  # Shape: (1, 2, 2)
    array2 = numpy.array([[5, 6], [7, 8]], dtype=numpy.uint8)[
        None, ...
    ]  # Shape: (1, 2, 2)

    dt1 = datetime(2023, 1, 1)
    dt2 = datetime(2023, 1, 2)
    data = {
        dt1: ImageData(array1),
        dt2: ImageData(array2),
    }

    result = _handle_raster_geotiff(data)
    assert isinstance(result, ImageData)
    assert result.array.shape == (2, 2, 2)  # 2 bands, 2x2 image
    assert (result.array[0] == array1[0]).all()
    assert (result.array[1] == array2[0]).all()
    assert len(result.band_descriptions) == 2


def test_handle_raster_geotiff_validation():
    """Test validation in _handle_raster_geotiff."""
    # Create 2x2 and 2x3 images for validation testing
    array1 = numpy.array([[1, 2], [3, 4]], dtype=numpy.uint8)[
        None, ...
    ]  # Shape: (1, 2, 2)
    array2 = numpy.array([[5, 6, 7], [8, 9, 10]], dtype=numpy.uint8)[
        None, ...
    ]  # Shape: (1, 2, 3)

    dt1 = datetime(2023, 1, 1)
    dt2 = datetime(2023, 1, 2)
    data = {
        dt1: ImageData(array1),
        dt2: ImageData(array2),
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
    data = numpy.array([[1, 2], [3, 4]], dtype=numpy.uint8)[
        None, ...
    ]  # Shape: (1, 2, 2)
    result = save_result(data, "png")
    assert isinstance(result, SaveResultData)
    assert result.media_type == "image/png"
    assert len(result.data) > 0  # Should contain PNG bytes


def test_save_result_geotiff():
    """Test save_result with multi-band GeoTIFF."""
    # Create test data
    array1 = numpy.array([[1, 2], [3, 4]], dtype=numpy.byte)[
        None, ...
    ]  # Shape: (1, 2, 2)
    array2 = numpy.array([[5, 6], [7, 8]], dtype=numpy.byte)[
        None, ...
    ]  # Shape: (1, 2, 2)
    dt1 = datetime(2023, 1, 1)
    dt2 = datetime(2023, 1, 2)
    data = RasterStack.from_images(
        {
            dt1: ImageData(array1),
            dt2: ImageData(array2),
        }
    )

    result = save_result(data, "gtiff")
    assert isinstance(result, SaveResultData)
    assert result.media_type == "image/tiff"
    assert len(result.data) > 0  # Should contain TIFF bytes


def _read_geotiff(result):
    """Open a SaveResultData GeoTIFF payload with rasterio."""
    import io as _io

    import rasterio

    return rasterio.open(_io.BytesIO(result.data))


def test_save_result_geotiff_preserves_float_single_band():
    """GTiff must preserve float dtype/values (not cast to uint8/RGB). Issue #296."""
    arr = numpy.ma.array(
        (numpy.linspace(-0.7, 0.7, 64, dtype="float32")).reshape(1, 8, 8),
        mask=numpy.zeros((1, 8, 8), dtype=bool),
    )
    data = RasterStack.from_images(
        {
            datetime(2021, 7, 1): ImageData(
                arr,
                crs="EPSG:4326",
                bounds=(-180, -90, 180, 90),
                band_descriptions=["ndvi"],
            )
        }
    )

    result = save_result(data, "GTiff")
    assert result.media_type == "image/tiff"
    with _read_geotiff(result) as ds:
        assert ds.count == 1  # single band, no RGB render / alpha band
        assert ds.dtypes[0] == "float32"  # native dtype preserved (not uint8)
        assert ds.crs is not None  # georeferenced
        band = ds.read(1)
        numpy.testing.assert_allclose(band.min(), -0.7, atol=1e-3)
        numpy.testing.assert_allclose(band.max(), 0.7, atol=1e-3)


def test_save_result_geotiff_float_masked_uses_nodata():
    """Masked float pixels are encoded as NaN nodata, real values preserved."""
    a = numpy.ones((1, 4, 4), dtype="float32") * 0.42
    mask = numpy.zeros((1, 4, 4), dtype=bool)
    mask[0, 0, 0] = True
    data = RasterStack.from_images(
        {
            datetime(2021, 7, 1): ImageData(
                numpy.ma.array(a, mask=mask),
                crs="EPSG:4326",
                bounds=(-180, -90, 180, 90),
                band_descriptions=["ndvi"],
            )
        }
    )

    result = save_result(data, "gtiff")
    with _read_geotiff(result) as ds:
        assert ds.dtypes[0] == "float32"
        assert numpy.isnan(ds.nodata)
        band = ds.read(1)
        assert numpy.isnan(band[0, 0])  # masked pixel -> nodata
        assert numpy.nanmax(band) == numpy.float32(0.42)  # values preserved


def test_save_result_geotiff_multi_slice_preserves_float():
    """Multi-slice float cube -> multi-band float GeoTIFF (was uint8 RGB). Issue #296."""

    def _img(v):
        a = numpy.ones((1, 4, 4), dtype="float32") * v
        return ImageData(
            numpy.ma.array(a, mask=numpy.zeros_like(a, dtype=bool)),
            crs="EPSG:4326",
            bounds=(-180, -90, 180, 90),
            band_descriptions=["ndvi"],
        )

    data = RasterStack.from_images(
        {datetime(2021, 7, 1): _img(-0.5), datetime(2022, 7, 1): _img(0.3)}
    )
    result = save_result(data, "gtiff")
    with _read_geotiff(result) as ds:
        assert ds.count == 2  # one band per slice
        assert ds.dtypes[0] == "float32"
        numpy.testing.assert_allclose(ds.read(1).min(), -0.5, atol=1e-6)
        numpy.testing.assert_allclose(ds.read(2).max(), 0.3, atol=1e-6)


def test_save_result_single_image():
    """Test save_result with single image."""
    array = numpy.array([[1, 2], [3, 4]], dtype=numpy.uint8)[
        None, ...
    ]  # Shape: (1, 2, 2)
    data = RasterStack.from_images({datetime(2023, 1, 1): ImageData(array)})

    result = save_result(data, "png")
    assert isinstance(result, SaveResultData)
    assert result.media_type == "image/png"
    assert len(result.data) > 0  # Should contain PNG bytes


def test_save_result_multi_slice_single_frame_format_raises():
    """A multi-slice RasterStack cannot be saved to a single-frame format.

    This is the failure mode behind merge_cubes of two cubes with non-matching
    time labels: the overlap_resolver is never applied and both slices are
    carried through, leaving a 2-key stack that PNG/JPEG cannot represent.
    """
    array = numpy.array([[1, 2], [3, 4]], dtype=numpy.uint8)[None, ...]
    data = RasterStack.from_images(
        {
            datetime(2021, 6, 1): ImageData(array),
            datetime(2021, 8, 1): ImageData(array),
        }
    )

    with pytest.raises(ValueError, match="2 temporal slices"):
        save_result(data, "png")


def test_save_result_feature_collection():
    """Test save_result with FeatureCollection."""
    data = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "properties": {"values": {"2021-01-01": 1.0}}}
        ],
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
