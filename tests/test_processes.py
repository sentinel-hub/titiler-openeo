"""Tests for process implementations."""

from datetime import datetime

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
    array_apply,
    array_create,
    array_element,
    create_data_cube,
)
from titiler.openeo.processes.implementations.data_model import RasterStack
from titiler.openeo.processes.implementations.dem import hillshade
from titiler.openeo.processes.implementations.image import (
    color_formula,
    image_indexes,
    to_array,
)
from titiler.openeo.processes.implementations.indices import ndvi, ndwi
from titiler.openeo.processes.implementations.logic import and_, if_, or_
from titiler.openeo.processes.implementations.reduce import reduce_dimension


@pytest.fixture
def sample_image_data():
    """Create a sample ImageData object for testing."""
    # Create a 3-band image (e.g., RGB)
    data = np.ma.array(
        np.random.randint(0, 256, size=(3, 10, 10), dtype=np.uint8),
        mask=np.zeros((3, 10, 10), dtype=bool),
    )
    return ImageData(data, band_descriptions=["red", "green", "blue"])


@pytest.fixture
def sample_raster_stack(sample_image_data):
    """Create a sample RasterStack for testing."""
    # Create a second ImageData
    data2 = np.ma.array(
        np.random.randint(0, 256, size=(3, 10, 10), dtype=np.uint8),
        mask=np.zeros((3, 10, 10), dtype=bool),
    )
    image_data2 = ImageData(data2, band_descriptions=["red", "green", "blue"])

    # Return a proper RasterStack with two samples
    return RasterStack.from_images(
        {datetime(2021, 1, 1): sample_image_data, datetime(2021, 1, 2): image_data2}
    )


def test_lazy_raster_stack_from_images(sample_image_data, sample_raster_stack):
    """Test the RasterStack.from_images factory method."""
    # Test creating from single ImageData
    dt = datetime.now()
    result = RasterStack.from_images({dt: sample_image_data})
    assert isinstance(result, RasterStack)
    assert dt in result
    assert result[dt].array.shape == sample_image_data.array.shape


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
        assert img_data.band_descriptions == ["red"]  # First band name


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
        assert img_data.band_descriptions == ["ndvi"]  # Should be named "ndvi"


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
        assert img_data.band_descriptions == ["ndwi"]  # Should be named "ndwi"


def test_apply(sample_raster_stack):
    """Test the apply function."""

    # Apply a simple function that doubles values. The callback receives a lazy
    # array view, so realize it like a real openEO callback would.
    def double(x, **kwargs):
        return np.asarray(x).astype(float) * 2

    result = apply(sample_raster_stack, double)

    assert isinstance(result, dict)
    assert len(result) == len(sample_raster_stack)

    # Each result should be an ImageData with same shape but doubled values
    for key, img_data in result.items():
        assert isinstance(img_data, ImageData)
        assert img_data.count == 3  # Same band count
        # Check if values are approximately doubled
        original = sample_raster_stack[key].array.data.astype("float")
        doubled = img_data.array.data.astype("float")
        np.testing.assert_array_equal(doubled, original * 2)


def test_array_element(sample_raster_stack):
    """Test the array_element function."""
    # Extract first band
    result = array_element(sample_raster_stack, 0)

    assert isinstance(result, np.ndarray)
    assert result.shape == (2, 10, 10)  # 2 images, 10x10 pixels


def test_reduce_dimension_spectral(sample_raster_stack):
    """Test reducing spectral dimension of RasterStack."""

    # Mean reducer for spectral dimension
    # Receives shape (bands, time, height, width) and reduces along bands (axis=0)
    def mean_reducer(data, **kwargs):
        # Data is a numpy array, not ImageData or dict
        if isinstance(data, (np.ndarray, np.ma.MaskedArray)):
            # Reduce along axis 0 (bands dimension)
            return np.mean(data, axis=0)
        return None

    result = reduce_dimension(sample_raster_stack, mean_reducer, "spectral")

    assert isinstance(result, dict)
    assert len(result) == len(sample_raster_stack)

    # Each result should be an ImageData with reduced spectral dimension
    for _key, img_data in result.items():
        assert isinstance(img_data, ImageData)
        # The spectral dimension should be reduced
        # Result shape should be (height, width) or (1, height, width)
        assert img_data.array.ndim in [2, 3]
        if img_data.array.ndim == 3:
            # If 3D, should have fewer bands than original (original had 3: red, green, blue)
            assert img_data.count <= 3


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
        dem_data[key] = ImageData(single_band, band_descriptions=["elevation"])

    result = hillshade(dem_data)

    assert isinstance(result, dict)
    assert len(result) == len(dem_data)

    # Each result should be an ImageData with hillshade data
    for img_data in result.values():
        assert isinstance(img_data, ImageData)
        assert img_data.count == 1  # Hillshade is single band
        assert img_data.band_descriptions == [
            "hillshade"
        ]  # Should be named "hillshade"


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


def test_array_apply_with_simple_process():
    """Test array_apply with a simple doubling process."""

    # Define a process that doubles values
    def double_process(x, positional_parameters=None, named_parameters=None):
        """Simple process that doubles the input value."""
        return x * 2

    # Test with 1D array
    data = np.array([1, 2, 3, 4, 5])
    result = array_apply(data, double_process)

    assert isinstance(result, np.ndarray)
    assert np.array_equal(result, np.array([2, 4, 6, 8, 10]))


def test_array_apply_with_index_parameter():
    """Test array_apply with process that uses index parameter."""

    # Define a process that multiplies value by index
    def multiply_by_index(x, positional_parameters=None, named_parameters=None):
        """Process that multiplies value by its index."""
        index = named_parameters.get("index", 0)
        return x * index

    # Test with 1D array
    data = np.array([10, 20, 30, 40])
    result = array_apply(data, multiply_by_index)

    # Expected: [10*0, 20*1, 30*2, 40*3] = [0, 20, 60, 120]
    assert isinstance(result, np.ndarray)
    assert np.array_equal(result, np.array([0, 20, 60, 120]))


def test_array_apply_with_2d_array():
    """Test array_apply with 2D array."""

    # Define a process that doubles each row (element from first dimension)
    def double_process(x, positional_parameters=None, named_parameters=None):
        """Process that doubles the input element (which is a row)."""
        return x * 2

    # Test with 2D array - iterates over first dimension (rows)
    data = np.array([[1, 2, 3], [4, 5, 6]])
    result = array_apply(data, double_process)

    assert isinstance(result, np.ndarray)
    # Result should have 2 elements (2 rows)
    assert len(result) == 2
    # Each row should be doubled
    expected = np.array([[2, 4, 6], [8, 10, 12]])
    np.testing.assert_array_equal(result, expected)


def test_array_apply_with_context():
    """Test array_apply with context parameter."""

    # Define a process that uses context
    def add_context_value(x, positional_parameters=None, named_parameters=None):
        """Process that adds context value to input."""
        context = named_parameters.get("context", 0)
        context_val = context if context is not None else 0
        return x + context_val

    # Test with context
    data = np.array([1, 2, 3, 4])
    result = array_apply(data, add_context_value, context=100)

    assert isinstance(result, np.ndarray)
    expected = np.array([101, 102, 103, 104])
    assert np.array_equal(result, expected)


def test_array_apply_with_float_array():
    """Test array_apply with floating point array."""

    # Define a vectorized process that computes square root (nan for negatives)
    def sqrt_process(x, positional_parameters=None, named_parameters=None):
        """Process that computes square root."""
        return np.sqrt(np.where(x >= 0, x, np.nan))

    # Test with float array
    data = np.array([1.0, 4.0, 9.0, 16.0, 25.0])
    result = array_apply(data, sqrt_process)

    assert isinstance(result, np.ndarray)
    expected = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
    np.testing.assert_array_almost_equal(result, expected)


def test_array_apply_with_scalar():
    """Test array_apply with scalar input (ndim == 0)."""

    # Define a process that squares the value
    def square_process(x, positional_parameters=None, named_parameters=None):
        """Process that squares the input."""
        return x * x

    # Test with scalar (numpy scalar, ndim=0)
    data = np.array(5)
    result = array_apply(data, square_process)

    assert isinstance(result, np.ndarray)
    # Scalar converted to 1D array with single element
    assert result.shape == (1,)
    assert result[0] == 25


def test_array_apply_with_negative_values():
    """Test array_apply with negative values."""

    # Define a process that computes absolute value
    def abs_process(x, positional_parameters=None, named_parameters=None):
        """Process that computes absolute value."""
        return abs(x)

    # Test with array containing negative values
    data = np.array([-5, -3, 0, 3, 5])
    result = array_apply(data, abs_process)

    assert isinstance(result, np.ndarray)
    expected = np.array([5, 3, 0, 3, 5])
    assert np.array_equal(result, expected)


def test_array_apply_with_3d_array():
    """Test array_apply with 3D array."""

    # Define a process that adds 10 to each 2D element
    def add_ten(x, positional_parameters=None, named_parameters=None):
        """Process that adds 10 to the input element."""
        return x + 10

    # Test with 3D array - iterates over first dimension (2D matrices)
    data = np.array([[[1, 2], [3, 4]], [[5, 6], [7, 8]]])
    result = array_apply(data, add_ten)

    assert isinstance(result, np.ndarray)
    # Result should have 2 elements (2 matrices in first dimension)
    assert len(result) == 2
    # Each matrix should have 10 added
    expected = np.array([[[11, 12], [13, 14]], [[15, 16], [17, 18]]])
    np.testing.assert_array_equal(result, expected)


def test_array_apply_label_parameter_none():
    """Test that label parameter is None for non-dict arrays."""
    # Define a process that checks if label is None
    labels_received = []

    def label_check_process(x, positional_parameters=None, named_parameters=None):
        """Process that records the label parameter."""
        label = named_parameters.get("label")
        labels_received.append(label)
        return x

    # Test with regular array (non-dict)
    data = np.array([1, 2, 3])
    result = array_apply(data, label_check_process)

    assert isinstance(result, np.ndarray)
    # The callback is evaluated once (vectorized) and label is None for plain arrays
    assert labels_received == [None]


def test_array_apply_index_broadcasts_over_leading_axis():
    """index is the position along the leading axis, broadcast over each element."""

    # Add each element's index to the element (element = a row here)
    def add_index(x, positional_parameters=None, named_parameters=None):
        return x + named_parameters["index"]

    # 2D array: 2 elements along axis 0
    data = np.array([[1, 2], [3, 4]])
    result = array_apply(data, add_index)

    assert isinstance(result, np.ndarray)
    assert len(result) == 2
    # row 0 += 0, row 1 += 1
    np.testing.assert_array_equal(result, np.array([[1, 2], [4, 5]]))


def test_array_apply_with_empty_dict():
    """Test array_apply with empty dict (edge case for branch coverage)."""

    # Define a process that records label
    def record_label_process(x, positional_parameters=None, named_parameters=None):
        """Process that records label."""
        return named_parameters.get("label")

    # Test with empty dict (degenerate input; array_apply is array-only)
    data = {}
    result = array_apply(data, record_label_process)

    # The callback is evaluated once and label is None for non-labeled input.
    assert np.asarray(result).item() is None


def test_array_apply_preserves_element_type():
    """Test that array_apply preserves element type from first dimension iteration."""

    # Define a process that doubles the element
    def double_process(x, positional_parameters=None, named_parameters=None):
        """Process that doubles the input."""
        if isinstance(x, np.ndarray):
            return x * 2
        return x * 2

    # Test with 2D array - should iterate over rows (first dimension)
    data = np.array([[1, 2], [3, 4]])
    result = array_apply(data, double_process)

    assert isinstance(result, np.ndarray)
    # Result should have 2 elements (2 rows)
    assert len(result) == 2
    # Each row should be doubled
    np.testing.assert_array_equal(result[0], np.array([2, 4]))
    np.testing.assert_array_equal(result[1], np.array([6, 8]))


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
    assert isinstance(result, RasterStack)
    assert len(result) == 1
    # Get the first (and only) ImageData
    first_img = result.first
    assert isinstance(first_img, ImageData)
    assert first_img.metadata["dimension"] == "temporal"
    assert first_img.metadata["label"] == "2021-01"
    assert first_img.metadata["type"] == "temporal"

    # Test with non-empty data cube
    # First create a cube with some data
    data = np.ma.masked_array(
        np.random.randint(0, 256, size=(1, 10, 10), dtype=np.uint8),
        mask=np.zeros((1, 10, 10), dtype=bool),
    )
    cube = RasterStack.from_images({datetime(2021, 1, 1): ImageData(data)})

    # Add a bands dimension
    result = add_dimension(data=cube, name="bands", label="red", type="bands")
    assert isinstance(result, RasterStack)
    assert len(result) == 2  # Original + new dimension
    # Get the newly added image (most recent timestamp)
    timestamps = list(result.keys())
    new_timestamp = max(timestamps)
    new_img = result[new_timestamp]
    assert isinstance(new_img, ImageData)
    # Should match spatial dimensions of existing data
    original_img = result[min(timestamps)]
    assert new_img.height == original_img.height
    assert new_img.width == original_img.width
    assert new_img.metadata["dimension"] == "bands"
    assert new_img.metadata["label"] == "red"
    assert new_img.metadata["type"] == "bands"

    # Test error cases
    # Cannot add spatial dimension
    with pytest.raises(ValueError, match="Cannot add spatial dimensions"):
        add_dimension(data=result, name="x", label="1", type="spatial")


def test_apply_dimension_temporal(sample_raster_stack):
    """Test apply_dimension on temporal dimension."""

    # Define a process that doubles values
    def double_process(data, **kwargs):
        """Process that doubles all values in the temporal series."""
        # data is a lazy array view (n_times, bands, height, width); realize it
        # like a real consumer (e.g. array_apply) would.
        return np.asarray(data).astype(float) * 2

    result = apply_dimension(sample_raster_stack, double_process, "temporal")

    assert isinstance(result, dict)
    assert len(result) == len(sample_raster_stack)

    # Each result should have doubled values
    for key, img_data in result.items():
        assert isinstance(img_data, ImageData)
        assert img_data.count == 3  # Same band count
        original = sample_raster_stack[key].array.data.astype("float")
        doubled = img_data.array.data.astype("float")
        np.testing.assert_array_equal(doubled, original * 2)


def test_apply_dimension_temporal_with_array_apply(sample_raster_stack):
    """Regression: array_apply works as the apply_dimension temporal callback.

    Previously apply_dimension passed the whole RasterStack to the temporal
    callback, so array_apply (array-only per its spec) rejected it with
    ``TypeError: Parameter 'data' in process 'array_apply': expected 'array'
    but got 'datacube'``. apply_dimension now passes an array-like lazy view, so
    array_apply works as the callback without needing to accept a RasterStack.
    """

    def double(x, **kwargs):
        return x * 2.0

    def temporal_callback(data, **kwargs):
        # data is the lazy array view; array_apply maps over the temporal dimension
        return array_apply(data, double)

    result = apply_dimension(sample_raster_stack, temporal_callback, "temporal")

    assert isinstance(result, dict)
    assert len(result) == len(sample_raster_stack)
    for key, img_data in result.items():
        original = sample_raster_stack[key].array.data.astype("float")
        doubled = img_data.array.data.astype("float")
        np.testing.assert_array_equal(doubled, original * 2)


def test_apply_dimension_temporal_with_target(sample_raster_stack):
    """Test apply_dimension on temporal dimension with target_dimension."""

    # Define a process that returns mean across time
    def mean_process(data, **kwargs):
        """Process that computes mean across temporal dimension."""
        # data is a lazy array view (n_times, bands, height, width)
        return np.array([np.mean(np.asarray(data), axis=0)])

    result = apply_dimension(
        sample_raster_stack, mean_process, "temporal", target_dimension="mean_time"
    )

    assert isinstance(result, RasterStack)
    assert len(result) == 1  # Collapsed to single result
    # With datetime keys, target_dimension is stored in metadata, not as key
    assert result.first is not None

    img_data = result.first
    assert isinstance(img_data, ImageData)
    assert img_data.count == 3  # Same band count
    # target_dimension should be in metadata
    assert img_data.metadata.get("target_dimension") == "mean_time"


def test_apply_dimension_spectral_single_image(sample_image_data):
    """Test apply_dimension on spectral dimension with single image."""

    # Convert to RasterStack
    dt = datetime.now()
    stack = RasterStack.from_images({dt: sample_image_data})

    # Define a process that normalizes bands
    def normalize_process(data, **kwargs):
        """Process that normalizes band values."""
        # data is now a numpy array (bands, height, width)
        array = data.astype(float)
        # Normalize to 0-1
        normalized = (array - array.min()) / (array.max() - array.min() + 1e-10)
        return normalized

    result = apply_dimension(stack, normalize_process, "spectral")

    assert isinstance(result, RasterStack)
    assert len(result) == 1
    # Keys are now datetime objects
    assert result.first is not None

    img_data = result.first
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
        original = sample_raster_stack[key].array.data.astype("float")
        added = img_data.array.data.astype("float")
        np.testing.assert_array_equal(added, original + 10)


def test_apply_dimension_single_temporal_image(sample_image_data):
    """Test apply_dimension with single temporal image (no temporal dimension)."""

    # Convert to RasterStack
    dt = datetime.now()
    stack = RasterStack.from_images({dt: sample_image_data})

    # Define a process (never invoked for a single-image stack)
    def some_process(data, **kwargs):
        return data

    # Should return unchanged when only one temporal image
    result = apply_dimension(stack, some_process, "temporal")

    assert isinstance(result, RasterStack)
    assert len(result) == 1
    assert result.first is not None  # Has same content


def test_apply_dimension_with_context(sample_raster_stack):
    """Test apply_dimension with context parameter."""

    # Define a process that uses context
    def context_process(data, **kwargs):
        """Process that uses context value."""
        context = kwargs.get("named_parameters", {}).get("context", {})
        multiplier = context.get("multiplier", 1)
        # data is a lazy array view (n_times, bands, height, width)
        return np.asarray(data).astype(float) * multiplier

    context = {"multiplier": 3}
    result = apply_dimension(
        sample_raster_stack, context_process, "temporal", context=context
    )

    assert isinstance(result, dict)
    assert len(result) == len(sample_raster_stack)

    # Each result should have values tripled
    for key, img_data in result.items():
        assert isinstance(img_data, ImageData)
        original = sample_raster_stack[key].array.data.astype("float")
        tripled = img_data.array.data.astype("float")
        np.testing.assert_array_equal(tripled, original * 3)


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


def test_if_with_numpy_arrays():
    """Test if function with a numpy boolean mask (element-wise)."""
    result = if_(np.array([True, False, True]), 1, 0)
    np.testing.assert_array_equal(result, np.array([1, 0, 1]))


def test_if_aligns_leading_spectral_dimension():
    """if_ should broadcast a spatial mask against spectral-leading operands.

    Inside an ``apply_dimension`` callback over the spectral dimension the
    band axis is leading: a constant band vector is ``(bands,)`` and a band
    cube is ``(bands, H, W)``, while the condition is a spatial ``(H, W)``
    mask. numpy.where aligns trailing axes, so these must be re-aligned.
    """
    value = np.zeros((4, 5), dtype=bool)
    value[1, 2] = True
    accept = np.array([0, 0, 1])  # constant band vector -> (3,)
    reject = np.arange(3 * 4 * 5).reshape(3, 4, 5)  # band cube -> (3, 4, 5)

    result = if_(value, accept, reject)

    assert result.shape == (3, 4, 5)
    # The single True pixel takes the accept band vector...
    np.testing.assert_array_equal(result[:, 1, 2], accept)
    # ...every other pixel keeps the reject cube value.
    mask = np.ones((4, 5), dtype=bool)
    mask[1, 2] = False
    np.testing.assert_array_equal(result[:, mask], reject[:, mask])


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
    data2 = ImageData(data2_array, band_descriptions=["red", "green", "blue"])

    # Test that if returns the correct ImageData
    result = if_(True, data1, data2)
    assert result is data1

    result = if_(False, data1, data2)
    assert result is data2


def test_or_scalar():
    """Test logical OR with scalar boolean values."""
    assert or_(True, True) is True
    assert or_(True, False) is True
    assert or_(False, True) is True
    assert or_(False, False) is False

    # Non-boolean values are coerced (null/None treated as false)
    assert or_(1, 0) is True
    assert or_(0, None) is False


def test_or_with_arrays():
    """Test logical OR with numpy arrays (element-wise)."""
    result = or_(np.array([True, False, True]), np.array([False, False, True]))
    np.testing.assert_array_equal(result, np.array([True, False, True]))

    # Mixed array / scalar operand
    result = or_(np.array([True, False]), False)
    np.testing.assert_array_equal(result, np.array([True, False]))


def test_and_scalar():
    """Test logical AND with scalar boolean values."""
    assert and_(True, True) is True
    assert and_(True, False) is False
    assert and_(False, False) is False


def test_and_with_arrays():
    """Test logical AND with numpy arrays (element-wise)."""
    result = and_(np.array([True, False]), np.array([True, True]))
    np.testing.assert_array_equal(result, np.array([True, False]))


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
        """Process for temporal dimension - receives a lazy array view."""
        # data is a lazy array view (n_times, bands, height, width)
        return np.asarray(data).astype(float) * 2

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
