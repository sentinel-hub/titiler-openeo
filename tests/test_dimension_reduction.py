"""Test dimension reduction functionality."""

import numpy as np
import pytest
from rio_tiler.models import ImageData

from titiler.openeo.processes.implementations.reduce import (
    DimensionNotAvailable,
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
    """Mock spectral reducer that returns mean across bands.

    This reducer works with the stacked format from _reduce_spectral_dimension_stack:
    - Input shape: (bands, time, height, width) or (bands, height, width)
    - Output shape: (time, height, width) or (height, width)
    """
    # Input should be a masked array
    if not isinstance(data, np.ma.MaskedArray):
        data = np.ma.asarray(data)

    # Compute mean across band dimension (axis=0)
    # This reduces the bands dimension regardless of whether we have time or not
    result = np.ma.mean(data, axis=0)

    return result


class StatefulCachingReducer:
    """A stateful reducer that caches results to test single-invocation requirement.

    This reducer maintains a call counter and cache to verify it's only called once.
    If called multiple times with different data, results will be incorrect.
    """

    def __init__(self):
        """
        Docstring for __init__

        :param self: Description
        """
        self.call_count = 0
        self.cached_result = None

    def __call__(self, data):
        """Reduce by computing mean, but use cached result if available."""
        self.call_count += 1

        # Simulate caching behavior - if we have a cached result, use it
        # This would give WRONG results if called multiple times
        if self.cached_result is not None:
            # Return cached result (which would be wrong for different data)
            return self.cached_result

        # Compute fresh result and cache it
        if not isinstance(data, np.ma.MaskedArray):
            data = np.ma.asarray(data)

        # Reduce along axis 0 (bands dimension)
        result = np.ma.mean(data, axis=0)
        self.cached_result = result

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

    def test_temporal_reduction_with_pixel_selection_mean(self):
        """Test temporal reduction using a pixel selection method (mean)."""
        from titiler.openeo.processes.implementations.math import mean

        # Create test data with 3 time steps
        data = {}
        for i in range(3):
            array = np.ma.ones((2, 10, 10)) * (i + 1)  # Values: 1, 2, 3
            data[f"time_{i}"] = ImageData(
                array,
                assets=[f"asset_{i}"],
                crs="EPSG:4326",
                bounds=(-180, -90, 180, 90),
                band_names=["red", "green"],
            )

        # mean is a pixel selection reducer, so it should use the efficient path
        result = _reduce_temporal_dimension(data, mean)

        assert isinstance(result, dict)
        assert "reduced" in result
        reduced_img = result["reduced"]

        # Mean of 1, 2, 3 should be 2.0
        np.testing.assert_array_almost_equal(reduced_img.array.data, 2.0)


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

        # Wrap in single-item RasterStack as required by the unified function
        stack = {"single_image": img}
        result_stack = _reduce_spectral_dimension_stack(stack, mock_spectral_reducer)
        result = result_stack["single_image"]

        assert isinstance(result, ImageData)
        # After reduction from (4, 10, 10) to (10, 10), result should be (10, 10)
        # But ImageData may reshape it to (1, 10, 10) or keep as (10, 10)
        assert result.array.shape[1:] == (
            10,
            10,
        ), f"Expected spatial dims (10, 10), got {result.array.shape}"

        # Values should be mean of 1, 2, 3, 4 = 2.5
        np.testing.assert_array_almost_equal(result.array.data, 2.5)

        # Check metadata
        assert result.metadata["reduced_dimension"] == "spectral"
        assert result.metadata["reduction_method"] == "mock_spectral_reducer"

    def test_spectral_reduction_single_image_band_names_cleared(self):
        """Test that band_names are cleared when spectral reduction changes band count.

        This prevents IndexError when trying to access bands by index after reduction.
        If we have 4 input bands but 1 output band, keeping the original band_names
        would cause mismatches.
        """
        # Create image with 4 bands and band names
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

        # Wrap in single-item RasterStack as required by the unified function
        stack = {"single_image": img}
        result_stack = _reduce_spectral_dimension_stack(stack, mock_spectral_reducer)
        result = result_stack["single_image"]

        # After reducing 4 bands to 1, band_names should be cleared to avoid mismatch
        # The result should have empty band_names since we can't know which band it represents
        assert (
            result.band_names == []
        ), f"Expected empty band_names, got {result.band_names}"

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

    def test_spectral_reduction_stack_with_stateful_reducer(self):
        """Test spectral reduction with a stateful/caching reducer.

        This is a CRITICAL test that ensures the reducer is called exactly ONCE.
        This test catches the bug where reducer was called per-image, which would
        break reducers with internal state/caching.
        """
        # Create test data with 3 time steps, each with 4 bands
        data = {}
        for i in range(3):
            array = np.ma.ones((4, 10, 10))
            array[0] = 1.0 + i  # Values change over time
            array[1] = 2.0 + i
            array[2] = 3.0 + i
            array[3] = 4.0 + i

            data[f"time_{i}"] = ImageData(
                array,
                assets=[f"asset_{i}"],
                crs="EPSG:4326",
                bounds=(-180, -90, 180, 90),
                band_names=["red", "green", "blue", "nir"],
            )

        # Use a stateful reducer that caches results
        stateful_reducer = StatefulCachingReducer()

        result = _reduce_spectral_dimension_stack(data, stateful_reducer)

        # CRITICAL: Verify the reducer was called EXACTLY ONCE
        assert stateful_reducer.call_count == 1, (
            f"Reducer must be called exactly once, but was called {stateful_reducer.call_count} times. "
            "This indicates the implementation is incorrectly calling the reducer multiple times, "
            "which breaks reducers with internal state/caching."
        )

        # Should return a stack with same temporal dimension
        assert isinstance(result, dict)
        assert len(result) == 3

        # Verify results are correct for each time slice
        # time_0: mean(1,2,3,4) = 2.5
        # time_1: mean(2,3,4,5) = 3.5
        # time_2: mean(3,4,5,6) = 4.5
        for i, key in enumerate(["time_0", "time_1", "time_2"]):
            assert key in result
            reduced_img = result[key]
            assert isinstance(reduced_img, ImageData)

            expected_value = 2.5 + i
            np.testing.assert_array_almost_equal(
                reduced_img.array.data,
                expected_value,
                decimal=5,
                err_msg=f"Time slice {i} has incorrect values",
            )

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

    def test_spectral_reduction_2d_output_matches_images(self):
        """Test spectral reduction with 2D output where first dim matches num images."""
        # Create test data with 2 time steps
        data = {}
        for i in range(2):
            array = np.ma.ones((3, 5, 5))
            data[f"time_{i}"] = ImageData(
                array,
                assets=[f"asset_{i}"],
                crs="EPSG:4326",
                bounds=(-180, -90, 180, 90),
            )

        # Reducer that returns 2D output where first dim matches num images
        def reducer_2d_time_height(data):
            # Reduce to (time, height) by averaging across bands and width
            return np.mean(data, axis=(0, 3))  # axis 0=bands, 3=width

        result = _reduce_spectral_dimension_stack(data, reducer_2d_time_height)
        assert len(result) == 2
        # Should add width dimension
        for img in result.values():
            assert img.array.ndim in [2, 3]  # Could be (h, w) or (1, h, w)

    def test_spectral_reduction_2d_output_spatial(self):
        """Test spectral reduction with 2D output representing spatial dims."""
        # Create test data with 1 time step
        data = {
            "time_0": ImageData(
                np.ma.ones((3, 5, 5)),
                assets=["asset_0"],
                crs="EPSG:4326",
                bounds=(-180, -90, 180, 90),
            )
        }

        # Reducer that returns 2D spatial output
        def reducer_2d_spatial(data):
            # Reduce to (height, width) by averaging across bands and time
            return np.mean(data, axis=(0, 1))  # axis 0=bands, 1=time

        result = _reduce_spectral_dimension_stack(data, reducer_2d_spatial)
        assert len(result) == 1
        # Should work correctly
        img = result["time_0"]
        assert img.array.ndim in [2, 3]

    def test_spectral_reduction_4d_output(self):
        """Test spectral reduction with 4D output (partial reduction)."""
        # Create test data with 2 time steps, each with 3 bands
        data = {}
        for i in range(2):
            array = np.ma.ones((3, 5, 5))
            data[f"time_{i}"] = ImageData(
                array,
                assets=[f"asset_{i}"],
                crs="EPSG:4326",
                bounds=(-180, -90, 180, 90),
                band_names=["red", "green", "blue"],
            )

        # Reducer that does partial reduction (3 bands -> 2 bands)
        def reducer_partial(data):
            # Input: (bands=3, time=2, h, w)
            # Output: (reduced_bands=2, time=2, h, w)
            # Take first 2 bands only
            return data[:2, :, :, :]

        result = _reduce_spectral_dimension_stack(data, reducer_partial)
        assert len(result) == 2
        # Each result should have 2 bands
        for img in result.values():
            assert img.array.shape[0] == 2
            # band_names should be cleared since count changed
            assert img.band_names == []

    def test_spectral_reduction_unexpected_dims(self):
        """Test spectral reduction with unexpected dimensionality (5D)."""
        # Create test data
        data = {
            "time_0": ImageData(
                np.ma.ones((3, 5, 5)),
                assets=["asset_0"],
                crs="EPSG:4326",
                bounds=(-180, -90, 180, 90),
            )
        }

        # Reducer that returns unexpected 5D output
        def reducer_5d(data):
            # Add extra dimensions
            return data[..., np.newaxis, np.newaxis]

        # Should still work but log a warning
        result = _reduce_spectral_dimension_stack(data, reducer_5d)
        assert len(result) == 1

    def test_spectral_reduction_with_failing_tasks(self):
        """Test spectral reduction when some tasks fail to load."""

        # Create a stack where some items fail to load
        class PartiallyFailingStack(dict):
            def __getitem__(self, key):
                if key == "fail_item":
                    raise KeyError(f"Task {key} failed")
                # For successful items, return ImageData
                return ImageData(
                    np.ma.ones((3, 5, 5)),
                    assets=[key],
                    crs="EPSG:4326",
                    bounds=(-180, -90, 180, 90),
                )

        failing_stack = PartiallyFailingStack()
        failing_stack["success_1"] = None  # Will be replaced by __getitem__
        failing_stack["fail_item"] = None  # Will raise KeyError
        failing_stack["success_2"] = None  # Will be replaced by __getitem__

        # Should process successfully and skip the failing item
        result = _reduce_spectral_dimension_stack(failing_stack, mock_spectral_reducer)
        # Should have 2 successful results
        assert len(result) == 2
        assert "success_1" in result
        assert "success_2" in result
        assert "fail_item" not in result


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
            # After reducing from 3 bands to scalar per pixel, expect (height, width) or (1, height, width)
            assert reduced_img.array.shape[-2:] == (
                4,
                4,
            ), f"Expected spatial dims (4, 4), got {reduced_img.array.shape}"
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
                raise KeyError(f"Task {key} failed")

        failing_stack = FailingStack({"item_1": None, "item_2": None})

        # The reducer should handle the failing tasks gracefully
        # KeyError exceptions are caught by the reducer, but when all tasks fail,
        # the reducer raises ValueError for no valid data
        with pytest.raises(ValueError, match="No valid data found"):
            _reduce_temporal_dimension(failing_stack, mock_temporal_reducer)
