"""Tests for process implementations."""

import numpy as np
import pytest
from rio_tiler.models import ImageData

from titiler.openeo.processes.implementations.apply import apply
from titiler.openeo.processes.implementations.arrays import (
    array_element,
    array_create,
    create_data_cube,
    add_dimension,
)
from titiler.openeo.processes.implementations.data_model import to_raster_stack
from titiler.openeo.processes.implementations.dem import hillshade
from titiler.openeo.processes.implementations.image import (
    color_formula,
    image_indexes,
    to_array,
)
from titiler.openeo.processes.implementations.indices import ndvi, ndwi
from titiler.openeo.processes.implementations.reduce import reduce_dimension


@pytest.fixture
def sample_image_data():
    """Create a sample ImageData object for testing."""
    # Create a 3-band image (e.g., RGB)
    data = np.ma.array(
        np.random.randint(0, 256, size=(3, 10, 10), dtype=np.uint8),
        mask=np.zeros((3, 10, 10), dtype=bool),
    )
    return ImageData(data, band_names=["red", "green", "blue"])


@pytest.fixture
def sample_raster_stack(sample_image_data):
    """Create a sample RasterStack (dict of ImageData) for testing."""
    # Create a second ImageData
    data2 = np.ma.array(
        np.random.randint(0, 256, size=(3, 10, 10), dtype=np.uint8),
        mask=np.zeros((3, 10, 10), dtype=bool),
    )
    image_data2 = ImageData(data2, band_names=["red", "green", "blue"])

    # Return a RasterStack with two samples
    return {"2021-01-01": sample_image_data, "2021-01-02": image_data2}


def test_to_raster_stack(sample_image_data, sample_raster_stack):
    """Test the to_raster_stack helper function."""
    # Test converting ImageData to RasterStack
    result = to_raster_stack(sample_image_data)
    assert isinstance(result, dict)
    assert "data" in result
    assert result["data"] is sample_image_data

    # Test passing RasterStack directly
    result = to_raster_stack(sample_raster_stack)
    assert result is sample_raster_stack


def test_image_indexes(sample_raster_stack):
    """Test the image_indexes function."""
    # Select first band from each image in the stack
    result = image_indexes(sample_raster_stack, [1])

    assert isinstance(result, dict)
    assert len(result) == len(sample_raster_stack)

    # Each result should be an ImageData with 1 band
    for _key, img_data in result.items():
        assert isinstance(img_data, ImageData)
        assert img_data.count == 1
        assert img_data.band_names == ["red"]  # First band name


def test_to_array(sample_raster_stack):
    """Test the to_array function."""
    result = to_array(sample_raster_stack)

    assert isinstance(result, dict)
    assert len(result) == len(sample_raster_stack)

    # Each result should be a numpy masked array
    for _key, array in result.items():
        assert isinstance(array, np.ma.MaskedArray)
        assert array.shape == (3, 10, 10)  # Same shape as original


def test_color_formula(sample_raster_stack):
    """Test the color_formula function."""
    # Apply a simple formula - format should be "OPERATION BANDS ARGS"
    result = color_formula(sample_raster_stack, "gamma rgb 1.5")

    assert isinstance(result, dict)
    assert len(result) == len(sample_raster_stack)

    # Each result should be an ImageData with 3 bands
    for _key, img_data in result.items():
        assert isinstance(img_data, ImageData)
        assert img_data.count == 3  # Should maintain 3 bands


def test_ndvi(sample_raster_stack):
    """Test the ndvi function."""
    # NDVI uses red (1) and nir (4) bands, but we only have 3 bands
    # Let's use bands 1 and 3 for this test
    result = ndvi(sample_raster_stack, 3, 1)

    assert isinstance(result, dict)
    assert len(result) == len(sample_raster_stack)

    # Each result should be an ImageData with 1 band
    for _key, img_data in result.items():
        assert isinstance(img_data, ImageData)
        assert img_data.count == 1  # NDVI results in a single band
        assert img_data.band_names == ["ndvi"]  # Should be named "ndvi"


def test_ndwi(sample_raster_stack):
    """Test the ndwi function."""
    # NDWI uses nir and swir bands, but we only have 3 bands
    # Let's use bands 2 and 3 for this test
    result = ndwi(sample_raster_stack, 2, 3)

    assert isinstance(result, dict)
    assert len(result) == len(sample_raster_stack)

    # Each result should be an ImageData with 1 band
    for _key, img_data in result.items():
        assert isinstance(img_data, ImageData)
        assert img_data.count == 1  # NDWI results in a single band
        assert img_data.band_names == ["ndwi"]  # Should be named "ndwi"


def test_apply(sample_raster_stack):
    """Test the apply function."""

    # Apply a simple function that doubles values
    def double(x, **kwargs):
        return x * 2

    result = apply(sample_raster_stack, double)

    assert isinstance(result, dict)
    assert len(result) == len(sample_raster_stack)

    # Each result should be an ImageData with same shape but doubled values
    for key, img_data in result.items():
        assert isinstance(img_data, ImageData)
        assert img_data.count == 3  # Same band count
        # Check if values are approximately doubled
        original = sample_raster_stack[key].array.data
        doubled = img_data.array.data
        assert np.allclose(doubled, original * 2)


def test_array_element(sample_raster_stack):
    """Test the array_element function."""
    # Extract first band
    result = array_element(sample_raster_stack, 0)

    assert isinstance(result, np.ndarray)
    assert result.shape == (2, 10, 10)  # 2 images, 10x10 pixels


def test_reduce_dimension_spectral(sample_raster_stack):
    """Test reducing spectral dimension of RasterStack."""

    # Mean reducer for spectral dimension
    def mean_reducer(data, **kwargs):
        if isinstance(data, ImageData):
            # For a single image, return mean across bands as a 2D array
            return np.mean(data.array, axis=0)
        elif isinstance(data, dict) and all(
            isinstance(v, ImageData) for v in data.values()
        ):
            # For a stack of images, return an array with shape (n_images,)
            return np.array([np.mean(img.array, axis=0) for img in data.values()])
        return None

    result = reduce_dimension(sample_raster_stack, mean_reducer, "spectral")

    assert isinstance(result, dict)
    assert len(result) == len(sample_raster_stack)

    # Each result should be an ImageData with 1 band (spectral dimension reduced)
    for _key, img_data in result.items():
        assert isinstance(img_data, ImageData)
        # The band dimension should be removed or reduced to 1
        assert img_data.array.ndim == 2 or img_data.count == 1


def test_hillshade(sample_raster_stack):
    """Test hillshade function with RasterStack."""
    # Create a DEM-like single band data
    dem_data = {}
    for key in sample_raster_stack:
        sample_raster_stack[key]
        single_band = np.ma.array(
            np.random.randint(0, 256, size=(1, 10, 10), dtype=np.uint8),
            mask=np.zeros((1, 10, 10), dtype=bool),
        )
        dem_data[key] = ImageData(single_band, band_names=["elevation"])

    result = hillshade(dem_data)

    assert isinstance(result, dict)
    assert len(result) == len(dem_data)

    # Each result should be an ImageData with hillshade data
    for img_data in result.values():
        assert isinstance(img_data, ImageData)
        assert img_data.count == 1  # Hillshade is single band
        assert img_data.band_names == ["hillshade"]  # Should be named "hillshade"

def test_array_create():
    """Test array_create function."""
    # Test empty array creation
    pixel = array_create()
    assert len(pixel) == 1

    # Test array creation with data
    data = [[1, 2, 3, 4], [5, 6, 7, 8]]
    result = array_create(data=data)
    assert isinstance(result, np.ndarray)
    assert np.array_equal(result, np.array(data))

def test_create_data_cube():
    """Test create_data_cube function."""
    result = create_data_cube()
    assert isinstance(result, dict)
    assert len(result) == 0

def test_add_dimension():
    """Test add_dimension function."""
    # Test with empty data cube
    cube = create_data_cube()
    
    # Add a temporal dimension to empty cube
    result = add_dimension(data=cube, name="temporal", label="2021-01", type="temporal")
    assert isinstance(result, dict)
    assert "temporal" in result
    assert isinstance(result["temporal"], ImageData)
    assert result["temporal"].metadata["dimension"] == "temporal"
    assert result["temporal"].metadata["label"] == "2021-01"
    assert result["temporal"].metadata["type"] == "temporal"

    # Test with non-empty data cube
    # First create a cube with some data
    data = np.ma.masked_array(
        np.random.randint(0, 256, size=(1, 10, 10), dtype=np.uint8),
        mask=np.zeros((1, 10, 10), dtype=bool),
    )
    cube = {"data": ImageData(data)}
    
    # Add a bands dimension
    result = add_dimension(data=cube, name="bands", label="red", type="bands")
    assert "bands" in result
    assert isinstance(result["bands"], ImageData)
    # Should match spatial dimensions of existing data
    assert result["bands"].height == cube["data"].height
    assert result["bands"].width == cube["data"].width
    assert result["bands"].metadata["dimension"] == "bands"
    assert result["bands"].metadata["label"] == "red"
    assert result["bands"].metadata["type"] == "bands"

    # Test error cases
    # Cannot add existing dimension
    with pytest.raises(ValueError, match="A dimension with name 'bands' already exists"):
        add_dimension(data=result, name="bands", label="green", type="bands")

    # Cannot add spatial dimension
    with pytest.raises(ValueError, match="Cannot add spatial dimensions"):
        add_dimension(data=result, name="x", label="1", type="spatial")
