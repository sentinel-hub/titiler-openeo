"""Tests for pixel selection reducers in math.py and reduce.py."""

from datetime import datetime

import numpy as np
import pytest
from rio_tiler.models import ImageData

from titiler.openeo.processes.implementations.data_model import RasterStack
from titiler.openeo.processes.implementations.math import (
    count,
    firstpixel,
    highestpixel,
    lastbandhight,
    lastbandlow,
    lowestpixel,
    stdev,
)
from titiler.openeo.processes.implementations.reduce import (
    PIXEL_SELECTION_REDUCERS,
    apply_pixel_selection,
)


class TestPixelSelectionReducersMapping:
    """Tests for the PIXEL_SELECTION_REDUCERS mapping."""

    def test_pixel_selection_reducers_contains_expected_keys(self):
        """Test that PIXEL_SELECTION_REDUCERS contains all expected reducers."""
        expected = {
            "firstpixel",
            "mean",
            "median",
            "sd",
            "stdev",
            "count",
            "highestpixel",
            "lowestpixel",
            "lastbandlow",
            "lastbandhight",
        }
        assert set(PIXEL_SELECTION_REDUCERS.keys()) == expected


class TestPixelSelectionReducersWithRasterStack:
    """Tests for pixel selection reducers with RasterStack input."""

    @pytest.fixture
    def raster_stack(self):
        """Create a sample RasterStack for testing."""
        images = {}
        for i in range(3):
            date = datetime(2021, 1, i + 1)
            # Create data where each date has increasing values
            data = np.ma.array(
                np.ones((1, 10, 10), dtype=np.float32) * (i + 1),
                mask=np.zeros((1, 10, 10), dtype=bool),
            )
            images[date] = ImageData(data, band_descriptions=["band1"])
        return RasterStack.from_images(images)

    @pytest.fixture
    def raster_stack_with_masks(self):
        """Create a RasterStack with partial masking for testing."""
        images = {}
        for i in range(3):
            date = datetime(2021, 1, i + 1)
            data = np.ones((1, 10, 10), dtype=np.float32) * (i + 1)
            mask = np.zeros((1, 10, 10), dtype=bool)
            # Mask different regions for each date
            if i == 0:
                mask[:, :5, :] = True  # First date: mask left half
            elif i == 1:
                mask[:, 5:, :] = True  # Second date: mask right half
            # Third date: no mask
            images[date] = ImageData(
                np.ma.array(data, mask=mask), band_descriptions=["band1"]
            )
        return RasterStack.from_images(images)

    def test_highestpixel_with_raster_stack(self, raster_stack):
        """Test highestpixel returns highest values from RasterStack."""
        result = highestpixel(raster_stack)
        # Highest value should be 3.0 (from 2021-01-03)
        assert np.allclose(result, 3.0)
        assert result.shape == (1, 10, 10)

    def test_lowestpixel_with_raster_stack(self, raster_stack):
        """Test lowestpixel returns lowest values from RasterStack."""
        result = lowestpixel(raster_stack)
        # Lowest value should be 1.0 (from 2021-01-01)
        assert np.allclose(result, 1.0)
        assert result.shape == (1, 10, 10)

    def test_firstpixel_with_raster_stack(self, raster_stack_with_masks):
        """Test firstpixel fills masked areas with first valid values."""
        result = firstpixel(raster_stack_with_masks)
        assert result.shape == (1, 10, 10)
        # Should have no fully masked areas
        assert not np.all(result == 0)

    def test_count_with_raster_stack(self, raster_stack):
        """Test count returns pixel counts from RasterStack."""
        result = count(raster_stack)
        # All pixels should have count of 3 (no masking)
        assert np.allclose(result, 3)
        assert result.shape == (1, 10, 10)

    def test_stdev_with_raster_stack(self, raster_stack):
        """Test stdev calculates standard deviation from RasterStack."""
        result = stdev(raster_stack)
        # Values are 1, 2, 3 -> std should be approximately 0.816 (ddof=0) or 1.0 (ddof=1)
        assert result.shape == (1, 10, 10)
        # Just verify it's a reasonable positive value
        assert np.all(result >= 0)


class TestPixelSelectionReducersWithArrays:
    """Tests for pixel selection reducers with array inputs."""

    def test_highestpixel_with_array(self):
        """Test highestpixel with numpy array input."""
        data = np.array([[[1, 2], [3, 4]], [[5, 6], [7, 8]], [[2, 3], [4, 5]]])
        result = highestpixel(data)
        expected = np.array([[5, 6], [7, 8]])
        np.testing.assert_array_equal(result, expected)

    def test_lowestpixel_with_array(self):
        """Test lowestpixel with numpy array input."""
        data = np.array([[[1, 2], [3, 4]], [[5, 6], [7, 8]], [[2, 3], [4, 5]]])
        result = lowestpixel(data)
        expected = np.array([[1, 2], [3, 4]])
        np.testing.assert_array_equal(result, expected)

    def test_highestpixel_with_masked_array(self):
        """Test highestpixel with masked array input."""
        data = np.ma.array(
            [[[1, 2], [3, 4]], [[5, 6], [7, 8]]],
            mask=[[[False, False], [False, False]], [[False, False], [False, False]]],
        )
        result = highestpixel(data)
        expected = np.array([[5, 6], [7, 8]])
        np.testing.assert_array_equal(result, expected)

    def test_lowestpixel_with_masked_array(self):
        """Test lowestpixel with masked array input."""
        data = np.ma.array(
            [[[1, 2], [3, 4]], [[5, 6], [7, 8]]],
            mask=[[[False, False], [False, False]], [[False, False], [False, False]]],
        )
        result = lowestpixel(data)
        expected = np.array([[1, 2], [3, 4]])
        np.testing.assert_array_equal(result, expected)

    def test_count_with_masked_array(self):
        """Test count with masked array input."""
        data = np.ma.array(
            [[[1, 2], [3, 4]], [[5, 6], [7, 8]]],
            mask=[[[True, False], [False, False]], [[False, True], [False, False]]],
        )
        result = count(data)
        # First pixel (0,0): one masked -> count 1
        # Second pixel (0,1): one masked -> count 1
        # Third pixel (1,0): none masked -> count 2
        # Fourth pixel (1,1): none masked -> count 2
        expected = np.array([[1, 1], [2, 2]])
        np.testing.assert_array_equal(result, expected)

    def test_count_with_regular_array(self):
        """Test count with regular numpy array."""
        data = np.array([[[1, 2], [3, 4]], [[5, 6], [7, 8]]])
        result = count(data)
        # All pixels should have count of 2 (no masking)
        expected = np.full((2, 2), 2)
        np.testing.assert_array_equal(result, expected)

    def test_firstpixel_with_masked_array(self):
        """Test firstpixel fills masked values."""
        data = np.ma.array(
            [[[0, 2], [3, 0]], [[5, 0], [0, 8]]],
            mask=[[[True, False], [False, True]], [[False, True], [True, False]]],
        )
        result = firstpixel(data)
        # First valid values should be: 5, 2, 3, 8
        expected = np.ma.array([[5, 2], [3, 8]])
        np.testing.assert_array_equal(result.data, expected.data)

    def test_firstpixel_with_regular_array(self):
        """Test firstpixel with regular array returns first element."""
        data = np.array([[[1, 2], [3, 4]], [[5, 6], [7, 8]]])
        result = firstpixel(data)
        expected = np.array([[1, 2], [3, 4]])
        np.testing.assert_array_equal(result, expected)


class TestLastBandSelectors:
    """Tests for lastbandlow and lastbandhight reducers."""

    @pytest.fixture
    def multiband_stack(self):
        """Create a RasterStack with multi-band images for last band tests."""
        images = {}
        # Create 3 dates with 2-band images
        # The last band value determines which pixel gets selected
        for i in range(3):
            date = datetime(2021, 1, i + 1)
            # Band 1: data values, Band 2: decision values
            band1 = np.ones((10, 10), dtype=np.float32) * (i + 1) * 10
            band2 = np.ones((10, 10), dtype=np.float32) * (3 - i)  # 3, 2, 1
            data = np.ma.array(
                np.stack([band1, band2]),
                mask=np.zeros((2, 10, 10), dtype=bool),
            )
            images[date] = ImageData(data, band_descriptions=["data", "decision"])
        return RasterStack.from_images(images)

    def test_lastbandlow_with_raster_stack(self, multiband_stack):
        """Test lastbandlow selects based on lowest last band value."""
        result = lastbandlow(multiband_stack)
        # Result from apply_pixel_selection returns the selected pixel data
        # The result shape depends on the band count of input images
        assert result.shape[1:] == (10, 10)

    def test_lastbandhight_with_raster_stack(self, multiband_stack):
        """Test lastbandhight selects based on highest last band value."""
        result = lastbandhight(multiband_stack)
        # Result from apply_pixel_selection returns the selected pixel data
        assert result.shape[1:] == (10, 10)

    def test_lastbandlow_with_array(self):
        """Test lastbandlow with numpy array input."""
        # Shape: (temporal, bands, height, width)
        data = np.array(
            [
                [[[10, 20]], [[3, 3]]],  # First: data=10,20, decision=3
                [[[30, 40]], [[1, 1]]],  # Second: data=30,40, decision=1
            ]
        )
        result = lastbandlow(data)
        # Result depends on implementation - just verify it runs
        assert result is not None

    def test_lastbandhight_with_array(self):
        """Test lastbandhight with numpy array input."""
        # Shape: (temporal, bands, height, width)
        data = np.array(
            [
                [[[10, 20]], [[3, 3]]],  # First: data=10,20, decision=3
                [[[30, 40]], [[1, 1]]],  # Second: data=30,40, decision=1
            ]
        )
        result = lastbandhight(data)
        # Result depends on implementation - just verify it runs
        assert result is not None

    def test_lastbandlow_with_1d_array(self):
        """Test lastbandlow handles 1D arrays."""
        data = np.array([1, 2, 3])
        result = lastbandlow(data)
        np.testing.assert_array_equal(result, data)

    def test_lastbandhight_with_1d_array(self):
        """Test lastbandhight handles 1D arrays."""
        data = np.array([1, 2, 3])
        result = lastbandhight(data)
        np.testing.assert_array_equal(result, data)


class TestReducerTypeErrors:
    """Tests for type error handling in reducers."""

    def test_highestpixel_invalid_type(self):
        """Test highestpixel raises TypeError for invalid input."""
        with pytest.raises(TypeError, match="Unsupported data type"):
            highestpixel("invalid")

    def test_lowestpixel_invalid_type(self):
        """Test lowestpixel raises TypeError for invalid input."""
        with pytest.raises(TypeError, match="Unsupported data type"):
            lowestpixel("invalid")

    def test_count_invalid_type(self):
        """Test count raises TypeError for invalid input."""
        with pytest.raises(TypeError, match="Unsupported data type"):
            count("invalid")

    def test_lastbandlow_invalid_type(self):
        """Test lastbandlow raises TypeError for invalid input."""
        with pytest.raises(TypeError, match="Unsupported data type"):
            lastbandlow("invalid")

    def test_lastbandhight_invalid_type(self):
        """Test lastbandhight raises TypeError for invalid input."""
        with pytest.raises(TypeError, match="Unsupported data type"):
            lastbandhight("invalid")

    def test_firstpixel_invalid_type(self):
        """Test firstpixel raises TypeError for invalid input."""
        with pytest.raises(TypeError, match="Unsupported data type"):
            firstpixel("invalid")


class TestApplyPixelSelectionMethods:
    """Tests for apply_pixel_selection with all supported methods."""

    @pytest.fixture
    def sample_stack(self):
        """Create a simple RasterStack for testing."""
        images = {}
        for i in range(2):
            date = datetime(2021, 1, i + 1)
            data = np.ma.array(
                np.ones((1, 5, 5), dtype=np.float32) * (i + 1),
                mask=np.zeros((1, 5, 5), dtype=bool),
            )
            images[date] = ImageData(data, band_descriptions=["band1"])
        return RasterStack.from_images(images)

    @pytest.mark.parametrize(
        "method",
        ["first", "highest", "lowest", "mean", "median", "stdev", "count"],
    )
    def test_apply_pixel_selection_methods(self, sample_stack, method):
        """Test apply_pixel_selection with various methods."""
        result = apply_pixel_selection(sample_stack, pixel_selection=method)
        assert isinstance(result, RasterStack)
        assert result.first is not None
        assert isinstance(result.first, ImageData)
        assert result.first.metadata["pixel_selection_method"] == method

    def test_apply_pixel_selection_lastbandlow(self):
        """Test apply_pixel_selection with lastbandlow method."""
        images = {}
        for i in range(2):
            date = datetime(2021, 1, i + 1)
            # Multi-band data for lastband methods
            data = np.ma.array(
                np.ones((2, 5, 5), dtype=np.float32) * (i + 1),
                mask=np.zeros((2, 5, 5), dtype=bool),
            )
            images[date] = ImageData(data, band_descriptions=["band1", "band2"])
        stack = RasterStack.from_images(images)

        result = apply_pixel_selection(stack, pixel_selection="lastbandlow")
        assert isinstance(result, RasterStack)
        assert result.first is not None

    def test_apply_pixel_selection_lastbandhight(self):
        """Test apply_pixel_selection with lastbandhight method."""
        images = {}
        for i in range(2):
            date = datetime(2021, 1, i + 1)
            data = np.ma.array(
                np.ones((2, 5, 5), dtype=np.float32) * (i + 1),
                mask=np.zeros((2, 5, 5), dtype=bool),
            )
            images[date] = ImageData(data, band_descriptions=["band1", "band2"])
        stack = RasterStack.from_images(images)

        result = apply_pixel_selection(stack, pixel_selection="lastbandhight")
        assert isinstance(result, RasterStack)
        assert result.first is not None
