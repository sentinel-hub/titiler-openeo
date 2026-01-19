"""Tests for process implementations."""

import numpy as np
import pytest
from rio_tiler.models import ImageData

from titiler.openeo.processes.implementations.apply import (
    DimensionNotAvailable,
    apply,
    apply_dimension,
)
from titiler.openeo.processes.implementations.arrays import (
    add_dimension,
    array_create,
    array_element,
    create_data_cube,
)
from titiler.openeo.processes.implementations.data_model import to_raster_stack
from titiler.openeo.processes.implementations.dem import hillshade
from titiler.openeo.processes.implementations.image import (
    color_formula,
    image_indexes,
    to_array,
)
from titiler.openeo.processes.implementations.indices import ndvi, ndwi
from titiler.openeo.processes.implementations.logic import if_
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
    with pytest.raises(
        ValueError, match="A dimension with name 'bands' already exists"
    ):
        add_dimension(data=result, name="bands", label="green", type="bands")

    # Cannot add spatial dimension
    with pytest.raises(ValueError, match="Cannot add spatial dimensions"):
        add_dimension(data=result, name="x", label="1", type="spatial")


def test_apply_dimension_temporal(sample_raster_stack):
    """Test apply_dimension on temporal dimension."""

    # Define a process that doubles values
    def double_process(data, **kwargs):
        """Process that doubles all values in the temporal series."""
        # data is a RasterStack
        result = []
        for img in data.values():
            result.append(img.array * 2)
        return np.array(result)

    result = apply_dimension(sample_raster_stack, double_process, "temporal")

    assert isinstance(result, dict)
    assert len(result) == len(sample_raster_stack)

    # Each result should have doubled values
    for key, img_data in result.items():
        assert isinstance(img_data, ImageData)
        assert img_data.count == 3  # Same band count
        original = sample_raster_stack[key].array.data
        doubled = img_data.array.data
        assert np.allclose(doubled, original * 2)


def test_apply_dimension_temporal_with_target(sample_raster_stack):
    """Test apply_dimension on temporal dimension with target_dimension."""

    # Define a process that returns mean across time
    def mean_process(data, **kwargs):
        """Process that computes mean across temporal dimension."""
        arrays = [img.array for img in data.values()]
        return np.array([np.mean(arrays, axis=0)])

    result = apply_dimension(
        sample_raster_stack, mean_process, "temporal", target_dimension="mean_time"
    )

    assert isinstance(result, dict)
    assert len(result) == 1  # Collapsed to single result
    assert "mean_time" in result

    img_data = result["mean_time"]
    assert isinstance(img_data, ImageData)
    assert img_data.count == 3  # Same band count


def test_apply_dimension_spectral_single_image(sample_image_data):
    """Test apply_dimension on spectral dimension with single image."""

    # Convert to RasterStack
    stack = to_raster_stack(sample_image_data)

    # Define a process that normalizes bands
    def normalize_process(data, **kwargs):
        """Process that normalizes band values."""
        # data is now a numpy array (bands, height, width)
        array = data.astype(float)
        # Normalize to 0-1
        normalized = (array - array.min()) / (array.max() - array.min() + 1e-10)
        return normalized

    result = apply_dimension(stack, normalize_process, "spectral")

    assert isinstance(result, dict)
    assert len(result) == 1
    assert "data" in result

    img_data = result["data"]
    assert isinstance(img_data, ImageData)
    assert img_data.count == 3  # Same band count
    # Values should be normalized (between 0 and 1)
    assert img_data.array.min() >= 0
    assert img_data.array.max() <= 1


def test_apply_dimension_spectral_stack(sample_raster_stack):
    """Test apply_dimension on spectral dimension with multi-image stack."""

    # Define a process that adds a constant to each band
    def add_constant_process(data, **kwargs):
        """Process that adds 10 to all band values."""
        # data is now a numpy array (bands, height, width)
        return data + 10

    result = apply_dimension(sample_raster_stack, add_constant_process, "bands")

    assert isinstance(result, dict)
    assert len(result) == len(sample_raster_stack)

    # Each result should have values increased by 10
    for key, img_data in result.items():
        assert isinstance(img_data, ImageData)
        assert img_data.count == 3  # Same band count
        original = sample_raster_stack[key].array.data
        added = img_data.array.data
        assert np.allclose(added, original + 10)


def test_apply_dimension_single_temporal_image(sample_image_data):
    """Test apply_dimension with single temporal image (no temporal dimension)."""

    # Convert to RasterStack
    stack = to_raster_stack(sample_image_data)

    # Define a process
    def some_process(data, **kwargs):
        return np.array([img.array for img in data.values()])

    # Should return unchanged when only one temporal image
    result = apply_dimension(stack, some_process, "temporal")

    assert isinstance(result, dict)
    assert len(result) == 1
    assert result == stack  # Should be unchanged


def test_apply_dimension_with_context(sample_raster_stack):
    """Test apply_dimension with context parameter."""

    # Define a process that uses context
    def context_process(data, **kwargs):
        """Process that uses context value."""
        context = kwargs.get("named_parameters", {}).get("context", {})
        multiplier = context.get("multiplier", 1)
        arrays = [img.array * multiplier for img in data.values()]
        return np.array(arrays)

    context = {"multiplier": 3}
    result = apply_dimension(
        sample_raster_stack, context_process, "temporal", context=context
    )

    assert isinstance(result, dict)
    assert len(result) == len(sample_raster_stack)

    # Each result should have values tripled
    for key, img_data in result.items():
        assert isinstance(img_data, ImageData)
        original = sample_raster_stack[key].array.data
        tripled = img_data.array.data
        assert np.allclose(tripled, original * 3)


def test_apply_dimension_unsupported_dimension(sample_raster_stack):
    """Test apply_dimension with unsupported dimension."""

    def dummy_process(data, **kwargs):
        return data

    # Should raise DimensionNotAvailable for unsupported dimension
    with pytest.raises(DimensionNotAvailable) as excinfo:
        apply_dimension(sample_raster_stack, dummy_process, "xyz")

    assert "xyz" in str(excinfo.value)


def test_if_basic():
    """Test basic if function behavior."""
    # Test true condition
    assert if_(True, "A", "B") == "A"

    # Test false condition
    assert if_(False, "A", "B") == "B"

    # Test null/None condition (treated as false)
    assert if_(None, "A", "B") == "B"


def test_if_with_arrays():
    """Test if function with array values."""
    # Test with lists
    result = if_(False, [1, 2, 3], [4, 5, 6])
    assert result == [4, 5, 6]

    result = if_(True, [1, 2, 3], [4, 5, 6])
    assert result == [1, 2, 3]


def test_if_with_numeric_values():
    """Test if function with numeric values."""
    # Test with integers
    assert if_(True, 123, 456) == 123
    assert if_(False, 123, 456) == 456

    # Test with floats
    assert if_(True, 1.5, 2.5) == 1.5
    assert if_(False, 1.5, 2.5) == 2.5


def test_if_default_reject():
    """Test if function with default reject value (None)."""
    # When value is true, return accept
    assert if_(True, 123) == 123

    # When value is false and reject is not provided, return None
    assert if_(False, 1) is None

    # When value is None and reject is not provided, return None
    assert if_(None, 1) is None


def test_if_with_complex_types():
    """Test if function with complex data types."""
    # Test with dictionaries
    accept_dict = {"key": "value1"}
    reject_dict = {"key": "value2"}
    assert if_(True, accept_dict, reject_dict) == accept_dict
    assert if_(False, accept_dict, reject_dict) == reject_dict

    # Test with mixed types
    assert if_(True, "string", 123) == "string"
    assert if_(False, "string", 123) == 123


def test_if_with_image_data(sample_image_data):
    """Test if function with ImageData objects."""
    # Create two different ImageData objects
    data1 = sample_image_data
    data2_array = np.ma.array(
        np.random.randint(0, 256, size=(3, 10, 10), dtype=np.uint8),
        mask=np.zeros((3, 10, 10), dtype=bool),
    )
    data2 = ImageData(data2_array, band_names=["red", "green", "blue"])

    # Test that if returns the correct ImageData
    result = if_(True, data1, data2)
    assert result is data1

    result = if_(False, data1, data2)
    assert result is data2


def test_apply_dimension_water_mask_preserves_dimensions(sample_raster_stack):
    """Test that water mask calculation preserves spatial dimensions."""

    def water_mask_proc(data, **kwargs):
        """Create a simple water mask - similar to user's example."""
        # data is a numpy array (bands, height, width)
        # Simulate simple water detection
        band1, band2, band3 = data[0], data[1], data[2]

        # Create boolean conditions
        condition1 = band1 > 100
        condition2 = band2 < 50
        condition3 = band3 > 150

        # Use nested if_ statements
        water_mask = if_(condition1, 1, if_(condition2, 1, if_(condition3, 1, 0)))

        return water_mask

    result = apply_dimension(sample_raster_stack, water_mask_proc, "spectral")

    assert isinstance(result, dict)
    assert len(result) == len(sample_raster_stack)

    # Each result should maintain spatial dimensions
    for key, img_data in result.items():
        assert isinstance(img_data, ImageData)
        # Should have spatial dimensions preserved
        assert img_data.height == sample_raster_stack[key].height
        assert img_data.width == sample_raster_stack[key].width
        # Should have 1 band (the mask)
        assert img_data.count == 1
        # Values should be binary (0 or 1)
        assert set(np.unique(img_data.array.data)).issubset({0, 1})


def test_apply_dimension_dimension_name_normalization(sample_raster_stack):
    """Test that dimension names are normalized correctly."""

    def temporal_process(data, **kwargs):
        """Process for temporal dimension - receives RasterStack."""
        arrays = [img.array * 2 for img in data.values()]
        return np.array(arrays)

    def spectral_process(data, **kwargs):
        """Process for spectral dimension - receives numpy array."""
        # data is now a numpy array (bands, height, width)
        return data * 2

    # Test various temporal dimension names
    for dim_name in ["temporal", "time", "t"]:
        result = apply_dimension(sample_raster_stack, temporal_process, dim_name)
        assert isinstance(result, dict)
        assert len(result) == len(sample_raster_stack)

    # Test various spectral dimension names
    for dim_name in ["spectral", "bands"]:
        result = apply_dimension(sample_raster_stack, spectral_process, dim_name)
        assert isinstance(result, dict)
        assert len(result) == len(sample_raster_stack)
