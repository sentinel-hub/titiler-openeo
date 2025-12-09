"""Test dimension reduction functionality."""

import numpy as np
import pytest
from rio_tiler.models import ImageData

from titiler.openeo.processes.implementations.reduce import (
    DimensionNotAvailable,
    _reduce_spectral_dimension_single_image,
    _reduce_spectral_dimension_stack,
    _reduce_temporal_dimension,
    reduce_dimension,
)


# Mock reducers for testing
def mock_temporal_reducer(data):
    """Mock temporal reducer that returns mean across time."""
    arrays = []
    for key in data.keys():
        try:
            img = data[key]
            arrays.append(img.array)
        except KeyError:
            continue

    if not arrays:
        raise ValueError("No valid data found")

    # Stack arrays and compute mean across time dimension
    stacked = np.ma.stack(arrays, axis=0)
    return np.ma.mean(stacked, axis=0)


def mock_spectral_reducer(data):
    """Mock spectral reducer that returns mean across bands."""
    # Input should be a masked array with shape (bands, height, width)
    if not isinstance(data, np.ma.MaskedArray):
        data = np.ma.asarray(data)

    # Compute mean across band dimension (axis=0)
    # This should reduce from (bands, height, width) to (height, width)
    result = np.ma.mean(data, axis=0)

    # Ensure we actually reduced the band dimension
    # If input was (bands, h, w), output should be (h, w)
    if result.ndim == data.ndim:
        # Squeeze out the band dimension if it wasn't properly reduced
        result = np.ma.squeeze(result, axis=0)

    return result


def mock_invalid_reducer_returns_dict(data):
    """Invalid reducer that returns a dict instead of array."""
    return {"invalid": "result"}


def mock_invalid_reducer_returns_string(data):
    """Invalid reducer that returns an object that can't be converted to array."""

    # Return an object that will definitely fail numpy.asarray conversion
    class UnconvertibleObject:
        def __array__(self):
            raise TypeError("Cannot convert to array")

    return UnconvertibleObject()


class TestTemporalDimensionReduction:
    """Test temporal dimension reduction."""

    def test_temporal_reduction_success(self):
        """Test successful temporal dimension reduction."""
        # Create test data with 3 time steps
        data = {}
        for i in range(3):
            array = np.ma.ones((2, 10, 10)) * (i + 1)  # Different values per time
            data[f"time_{i}"] = ImageData(
                array,
                assets=[f"asset_{i}"],
                crs="EPSG:4326",
                bounds=(-180, -90, 180, 90),
                band_names=["red", "green"],
            )

        result = _reduce_temporal_dimension(data, mock_temporal_reducer)

        # Should return a single-item RasterStack
        assert isinstance(result, dict)
        assert len(result) == 1
        assert "reduced" in result

        reduced_img = result["reduced"]
        assert isinstance(reduced_img, ImageData)
        assert reduced_img.array.shape == (2, 10, 10)  # Same spatial/spectral shape

        # Values should be mean of 1, 2, 3 = 2
        np.testing.assert_array_almost_equal(reduced_img.array.data, 2.0)

        # Check metadata
        assert reduced_img.metadata["reduced_dimension"] == "temporal"
        assert reduced_img.metadata["reduction_method"] == "mock_temporal_reducer"

    def test_temporal_reduction_empty_data(self):
        """Test temporal reduction with empty data."""
        with pytest.raises(ValueError, match="Expected a non-empty RasterStack"):
            _reduce_temporal_dimension({}, mock_temporal_reducer)

    def test_temporal_reduction_invalid_reducer_dict(self):
        """Test temporal reduction with reducer returning dict."""
        data = {
            "time_0": ImageData(
                np.ma.ones((2, 10, 10)),
                assets=["asset_0"],
                crs="EPSG:4326",
                bounds=(-180, -90, 180, 90),
            )
        }

        with pytest.raises(
            ValueError, match="must return an array-like object.*not a RasterStack"
        ):
            _reduce_temporal_dimension(data, mock_invalid_reducer_returns_dict)

    def test_temporal_reduction_invalid_reducer_string(self):
        """Test temporal reduction with reducer returning string."""
        data = {
            "time_0": ImageData(
                np.ma.ones((2, 10, 10)),
                assets=["asset_0"],
                crs="EPSG:4326",
                bounds=(-180, -90, 180, 90),
            )
        }

        with pytest.raises(ValueError, match="cannot be converted to an array"):
            _reduce_temporal_dimension(data, mock_invalid_reducer_returns_string)


class TestSpectralDimensionReduction:
    """Test spectral dimension reduction."""

    def test_spectral_reduction_single_image_success(self):
        """Test successful spectral reduction on single image."""
        # Create image with 4 bands
        array = np.ma.ones((4, 10, 10))
        array[0] = 1  # red
        array[1] = 2  # green
        array[2] = 3  # blue
        array[3] = 4  # nir

        img = ImageData(
            array,
            assets=["test_asset"],
            crs="EPSG:4326",
            bounds=(-180, -90, 180, 90),
            band_names=["red", "green", "blue", "nir"],
        )

        result = _reduce_spectral_dimension_single_image(img, mock_spectral_reducer)

        assert isinstance(result, ImageData)
        assert result.array.shape == (
            1,
            10,
            10,
        )  # Reduced to single band: (1, height, width)

        # Values should be mean of 1, 2, 3, 4 = 2.5
        np.testing.assert_array_almost_equal(result.array.data, 2.5)

        # Check metadata
        assert result.metadata["reduced_dimension"] == "spectral"
        assert result.metadata["reduction_method"] == "mock_spectral_reducer"

    def test_spectral_reduction_stack_success(self):
        """Test successful spectral reduction on stack."""
        # Create test data with 2 time steps, each with 3 bands
        data = {}
        for i in range(2):
            array = np.ma.ones((3, 5, 5))
            array[0] = 1 + i  # red: 1, 2
            array[1] = 2 + i  # green: 2, 3
            array[2] = 3 + i  # blue: 3, 4

            data[f"time_{i}"] = ImageData(
                array,
                assets=[f"asset_{i}"],
                crs="EPSG:4326",
                bounds=(-180, -90, 180, 90),
                band_names=["red", "green", "blue"],
            )

        result = _reduce_spectral_dimension_stack(data, mock_spectral_reducer)

        # Should return a stack with same temporal dimension
        assert isinstance(result, dict)
        assert len(result) == 2

        for i, key in enumerate(["time_0", "time_1"]):
            assert key in result
            reduced_img = result[key]
            assert isinstance(reduced_img, ImageData)
            assert reduced_img.array.shape == (
                1,
                5,
                5,
            )  # Reduced to single band: (1, height, width)

            # time_0: mean(1,2,3) = 2, time_1: mean(2,3,4) = 3
            expected_value = 2 + i
            np.testing.assert_array_almost_equal(reduced_img.array.data, expected_value)

    def test_spectral_reduction_stack_empty_data(self):
        """Test spectral reduction with empty stack."""
        with pytest.raises(ValueError, match="Expected a non-empty RasterStack"):
            _reduce_spectral_dimension_stack({}, mock_spectral_reducer)

    def test_spectral_reduction_invalid_reducer_dict(self):
        """Test spectral reduction with reducer returning dict."""
        data = {
            "time_0": ImageData(
                np.ma.ones((3, 5, 5)),
                assets=["asset_0"],
                crs="EPSG:4326",
                bounds=(-180, -90, 180, 90),
            )
        }

        with pytest.raises(
            ValueError, match="must return an array-like object.*not a RasterStack"
        ):
            _reduce_spectral_dimension_stack(data, mock_invalid_reducer_returns_dict)

    def test_spectral_reduction_invalid_reducer_string(self):
        """Test spectral reduction with reducer returning string."""
        data = {
            "time_0": ImageData(
                np.ma.ones((3, 5, 5)),
                assets=["asset_0"],
                crs="EPSG:4326",
                bounds=(-180, -90, 180, 90),
            )
        }

        with pytest.raises(ValueError, match="cannot be converted to an array"):
            _reduce_spectral_dimension_stack(data, mock_invalid_reducer_returns_string)


class TestReduceDimensionIntegration:
    """Integration tests for reduce_dimension function."""

    def test_reduce_temporal_dimension(self):
        """Test temporal dimension reduction via main function."""
        # Create test data with 2 time steps
        data = {}
        for i in range(2):
            array = np.ma.ones((2, 3, 3)) * (i + 1)
            data[f"time_{i}"] = ImageData(
                array,
                assets=[f"asset_{i}"],
                crs="EPSG:4326",
                bounds=(-180, -90, 180, 90),
            )

        # Test different dimension name variations
        for dim_name in ["temporal", "time", "t"]:
            result = reduce_dimension(data, mock_temporal_reducer, dim_name)

            assert isinstance(result, dict)
            assert len(result) == 1
            assert "reduced" in result

            reduced_img = result["reduced"]
            assert reduced_img.array.shape == (2, 3, 3)
            np.testing.assert_array_almost_equal(
                reduced_img.array.data, 1.5
            )  # mean(1,2)

    def test_reduce_spectral_dimension_single_image(self):
        """Test spectral dimension reduction on single image via main function."""
        array = np.ma.ones((3, 4, 4))
        array[0] = 1
        array[1] = 2
        array[2] = 3

        data = {
            "single": ImageData(
                array, assets=["asset"], crs="EPSG:4326", bounds=(-180, -90, 180, 90)
            )
        }

        # Test different dimension name variations
        for dim_name in ["spectral", "bands"]:
            result = reduce_dimension(data, mock_spectral_reducer, dim_name)

            assert isinstance(result, dict)
            assert len(result) == 1
            assert "single" in result

            reduced_img = result["single"]
            assert reduced_img.array.shape == (
                1,
                4,
                4,
            )  # Reduced to single band: (1, height, width)
            np.testing.assert_array_almost_equal(
                reduced_img.array.data, 2.0
            )  # mean(1,2,3)

    def test_reduce_spectral_dimension_stack(self):
        """Test spectral dimension reduction on stack via main function."""
        data = {}
        for i in range(2):
            array = np.ma.ones((2, 2, 2))
            array[0] = 1 + i
            array[1] = 2 + i

            data[f"time_{i}"] = ImageData(
                array,
                assets=[f"asset_{i}"],
                crs="EPSG:4326",
                bounds=(-180, -90, 180, 90),
            )

        result = reduce_dimension(data, mock_spectral_reducer, "bands")

        assert isinstance(result, dict)
        assert len(result) == 2

        # time_0: mean(1,2) = 1.5, time_1: mean(2,3) = 2.5
        for i, key in enumerate(["time_0", "time_1"]):
            reduced_img = result[key]
            expected_value = 1.5 + i
            np.testing.assert_array_almost_equal(reduced_img.array.data, expected_value)

    def test_reduce_single_item_temporal(self):
        """Test that single-item stack returns as-is for temporal reduction."""
        data = {
            "single": ImageData(
                np.ma.ones((2, 3, 3)),
                assets=["asset"],
                crs="EPSG:4326",
                bounds=(-180, -90, 180, 90),
            )
        }

        result = reduce_dimension(data, mock_temporal_reducer, "temporal")

        # Should return the original data unchanged
        assert result == data

    def test_unsupported_dimension(self):
        """Test error for unsupported dimension."""
        data = {
            "test": ImageData(
                np.ma.ones((2, 3, 3)),
                assets=["asset"],
                crs="EPSG:4326",
                bounds=(-180, -90, 180, 90),
            )
        }

        with pytest.raises(DimensionNotAvailable) as exc_info:
            reduce_dimension(data, mock_temporal_reducer, "xyz")

        assert exc_info.value.dimension == "xyz"
        assert "does not exist" in str(exc_info.value)


class TestErrorHandling:
    """Test error handling in dimension reduction."""

    def test_temporal_reduction_all_tasks_fail(self):
        """Test temporal reduction when all tasks in stack fail."""

        # Create a regular stack where accessing items raises errors
        class FailingStack(dict):
            def __getitem__(self, key):
                raise RuntimeError(f"Task {key} failed")

        failing_stack = FailingStack({"item_1": None, "item_2": None})

        # The reducer should handle the failing tasks gracefully
        # Since we can't access any data, it should raise during data access
        with pytest.raises(RuntimeError, match="Task .* failed"):
            _reduce_temporal_dimension(failing_stack, mock_temporal_reducer)
