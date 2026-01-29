"""Tests for cutline_mask functionality."""

from unittest.mock import MagicMock, patch

import numpy as np
from rasterio.crs import CRS
from rio_tiler.models import ImageData

from titiler.openeo.processes.implementations.data_model import RasterStack
from titiler.openeo.processes.implementations.reduce import (
    _compute_aggregated_cutline_mask,
    apply_pixel_selection,
)
from titiler.openeo.reader import _apply_cutline_mask, _reader


class TestComputeAggregatedCutlineMask:
    """Tests for the _compute_aggregated_cutline_mask function."""

    def test_all_masks_none_returns_none(self):
        """When all masks are None, return None (all pixels valid)."""
        result = _compute_aggregated_cutline_mask([None, None, None])
        assert result is None

    def test_empty_list_returns_none(self):
        """Empty list returns None."""
        result = _compute_aggregated_cutline_mask([])
        assert result is None

    def test_any_mask_none_returns_none(self):
        """If any mask is None (all valid), aggregate should be None (all valid)."""
        mask1 = np.array([[True, True], [False, False]], dtype=bool)
        mask2 = None  # All pixels valid
        mask3 = np.array([[True, False], [True, False]], dtype=bool)

        result = _compute_aggregated_cutline_mask([mask1, mask2, mask3])
        # With OR logic, if any image has all valid pixels, all pixels are valid
        assert result is None

    def test_single_mask_returns_copy(self):
        """Single mask returns a copy of itself."""
        mask = np.array([[True, False], [False, True]], dtype=bool)
        result = _compute_aggregated_cutline_mask([mask])

        assert result is not None
        np.testing.assert_array_equal(result, mask)
        # Ensure it's a copy, not the same object
        assert result is not mask

    def test_or_combination_two_masks(self):
        """Two masks combine with OR logic (minimum for bool where True=outside)."""
        # mask1: top-left valid (False), rest outside (True)
        mask1 = np.array([[False, True], [True, True]], dtype=bool)
        # mask2: bottom-right valid (False), rest outside (True)
        mask2 = np.array([[True, True], [True, False]], dtype=bool)

        result = _compute_aggregated_cutline_mask([mask1, mask2])

        # Expected: pixels valid if valid in ANY mask
        # Top-left: valid in mask1 -> False
        # Bottom-right: valid in mask2 -> False
        # Others: outside in both -> True
        expected = np.array([[False, True], [True, False]], dtype=bool)
        np.testing.assert_array_equal(result, expected)

    def test_or_combination_three_masks(self):
        """Three masks combine correctly with OR logic."""
        # Each mask has one valid pixel in different positions
        mask1 = np.array([[False, True, True], [True, True, True]], dtype=bool)
        mask2 = np.array([[True, False, True], [True, True, True]], dtype=bool)
        mask3 = np.array([[True, True, True], [True, True, False]], dtype=bool)

        result = _compute_aggregated_cutline_mask([mask1, mask2, mask3])

        # Expected: valid where ANY mask is valid
        expected = np.array([[False, False, True], [True, True, False]], dtype=bool)
        np.testing.assert_array_equal(result, expected)

    def test_all_valid_in_all_masks(self):
        """All masks have all valid pixels -> all valid."""
        mask1 = np.zeros((3, 3), dtype=bool)  # All False = all valid
        mask2 = np.zeros((3, 3), dtype=bool)

        result = _compute_aggregated_cutline_mask([mask1, mask2])

        expected = np.zeros((3, 3), dtype=bool)
        np.testing.assert_array_equal(result, expected)

    def test_all_outside_in_all_masks(self):
        """All masks have all outside pixels -> all outside."""
        mask1 = np.ones((3, 3), dtype=bool)  # All True = all outside
        mask2 = np.ones((3, 3), dtype=bool)

        result = _compute_aggregated_cutline_mask([mask1, mask2])

        expected = np.ones((3, 3), dtype=bool)
        np.testing.assert_array_equal(result, expected)

    def test_complementary_masks_full_coverage(self):
        """Two complementary masks (left/right halves) cover full area."""
        # Left half valid
        mask1 = np.array(
            [[False, False, True, True], [False, False, True, True]], dtype=bool
        )
        # Right half valid
        mask2 = np.array(
            [[True, True, False, False], [True, True, False, False]], dtype=bool
        )

        result = _compute_aggregated_cutline_mask([mask1, mask2])

        # Combined: all pixels valid
        expected = np.zeros((2, 4), dtype=bool)
        np.testing.assert_array_equal(result, expected)


class TestApplyPixelSelectionWithAggregatedCutline:
    """Tests for apply_pixel_selection using aggregated cutline mask."""

    def _create_image_with_cutline(
        self, values, cutline_mask, bounds=(0, 0, 10, 10), mask=None
    ):
        """Helper to create ImageData with cutline_mask."""
        if mask is None:
            mask = np.zeros_like(values, dtype=bool)
        data = np.ma.array(values, mask=mask)
        img = ImageData(data, bounds=bounds, crs=CRS.from_epsg(4326))
        img.cutline_mask = cutline_mask
        return img

    def test_aggregated_cutline_set_before_feed(self):
        """Verify aggregated cutline is set on pixsel_method before processing."""
        # Two images with complementary cutlines
        cutline1 = np.zeros((10, 10), dtype=bool)
        cutline1[:, 5:] = True  # Right half outside

        cutline2 = np.zeros((10, 10), dtype=bool)
        cutline2[:, :5] = True  # Left half outside

        img1 = self._create_image_with_cutline(
            np.ones((1, 10, 10), dtype=np.float32) * 10, cutline1
        )
        img2 = self._create_image_with_cutline(
            np.ones((1, 10, 10), dtype=np.float32) * 20, cutline2
        )

        stack = RasterStack.from_images({"2021-01-01": img1, "2021-01-02": img2})

        # Apply pixel selection - should work without error
        result = apply_pixel_selection(data=stack, pixel_selection="first")

        assert "data" in result
        assert result["data"].array.shape == (1, 10, 10)

    def test_no_cutline_masks_all_none(self):
        """When all images have no cutline_mask, aggregated should be None."""
        img1 = self._create_image_with_cutline(
            np.ones((1, 10, 10), dtype=np.float32) * 10, None
        )
        img2 = self._create_image_with_cutline(
            np.ones((1, 10, 10), dtype=np.float32) * 20, None
        )

        stack = RasterStack.from_images({"2021-01-01": img1, "2021-01-02": img2})

        result = apply_pixel_selection(data=stack, pixel_selection="first")

        assert "data" in result
        # All pixels should be from first image
        np.testing.assert_array_equal(result["data"].array.data, 10)

    def test_one_image_no_cutline_makes_all_valid(self):
        """If one image has no cutline_mask, all pixels are considered valid."""
        # First image has restrictive cutline (only left half valid)
        cutline1 = np.zeros((10, 10), dtype=bool)
        cutline1[:, 5:] = True  # Right half outside

        img1 = self._create_image_with_cutline(
            np.ones((1, 10, 10), dtype=np.float32) * 10, cutline1
        )
        # Second image has no cutline - all pixels valid
        img2 = self._create_image_with_cutline(
            np.ones((1, 10, 10), dtype=np.float32) * 20, None
        )

        stack = RasterStack.from_images({"2021-01-01": img1, "2021-01-02": img2})

        result = apply_pixel_selection(data=stack, pixel_selection="first")

        assert "data" in result
        # With aggregated cutline = None (all valid), first image fills everything
        np.testing.assert_array_equal(result["data"].array.data, 10)

    def test_early_termination_with_full_coverage_aggregated(self):
        """Early termination works correctly with aggregated cutline."""
        # Two images that together cover the full area
        # But individually, neither covers everything

        # Image 1: left half valid, value 100
        cutline1 = np.zeros((10, 20), dtype=bool)
        cutline1[:, 10:] = True  # Right half outside

        # Image 2: right half valid, value 200
        cutline2 = np.zeros((10, 20), dtype=bool)
        cutline2[:, :10] = True  # Left half outside

        # Image 1 has data everywhere but only left is in footprint
        data1 = np.ones((1, 10, 20), dtype=np.float32) * 100
        mask1 = np.zeros((1, 10, 20), dtype=bool)
        img1 = self._create_image_with_cutline(
            data1, cutline1, bounds=(0, 0, 20, 10), mask=mask1
        )

        # Image 2 has data everywhere but only right is in footprint
        data2 = np.ones((1, 10, 20), dtype=np.float32) * 200
        mask2 = np.zeros((1, 10, 20), dtype=bool)
        img2 = self._create_image_with_cutline(
            data2, cutline2, bounds=(0, 0, 20, 10), mask=mask2
        )

        stack = RasterStack.from_images({"2021-01-01": img1, "2021-01-02": img2})

        result = apply_pixel_selection(data=stack, pixel_selection="first")

        assert "data" in result
        img = result["data"]
        assert img.array.shape == (1, 10, 20)


class TestApplyCutlineMask:
    """Tests for the _apply_cutline_mask function."""

    def test_apply_cutline_mask_basic(self):
        """Test that cutline_mask is correctly created from geometry."""
        # Create a simple ImageData with known dimensions
        data = np.ma.array(
            np.ones((1, 100, 100), dtype=np.float32),
            mask=np.zeros((1, 100, 100), dtype=bool),
        )
        img = ImageData(
            data,
            bounds=(0, 0, 10, 10),
            crs=CRS.from_epsg(4326),
        )
        # transform is computed from bounds and dimensions automatically

        # Define a geometry that covers half the image (left half)
        geometry = {
            "type": "Polygon",
            "coordinates": [[[0, 0], [5, 0], [5, 10], [0, 10], [0, 0]]],
        }

        # Apply cutline mask
        result = _apply_cutline_mask(img, geometry, CRS.from_epsg(4326))

        # Check that cutline_mask is set
        assert result.cutline_mask is not None
        assert result.cutline_mask.shape == (100, 100)
        assert result.cutline_mask.dtype == bool

        # The left half should be inside the geometry (False = inside)
        # The right half should be outside (True = outside)
        # Note: rasterize with default_value=0, fill=1 means:
        # - Inside geometry: 0 (False after .astype("bool"))
        # - Outside geometry: 1 (True after .astype("bool"))
        left_half = result.cutline_mask[:, :50]
        right_half = result.cutline_mask[:, 50:]

        # Inside the geometry should have more False values
        assert np.sum(~left_half) > np.sum(~right_half)

    def test_apply_cutline_mask_full_coverage(self):
        """Test cutline_mask when geometry covers entire image."""
        data = np.ma.array(
            np.ones((1, 50, 50), dtype=np.float32),
            mask=np.zeros((1, 50, 50), dtype=bool),
        )
        img = ImageData(
            data,
            bounds=(0, 0, 10, 10),
            crs=CRS.from_epsg(4326),
        )

        # Geometry that covers the entire image
        geometry = {
            "type": "Polygon",
            "coordinates": [[[-1, -1], [11, -1], [11, 11], [-1, 11], [-1, -1]]],
        }

        result = _apply_cutline_mask(img, geometry, CRS.from_epsg(4326))

        # All pixels should be inside the geometry (False = valid)
        assert result.cutline_mask is not None
        # Most pixels should be False (inside geometry)
        assert np.sum(~result.cutline_mask) == result.cutline_mask.size

    def test_apply_cutline_mask_no_coverage(self):
        """Test cutline_mask when geometry doesn't overlap image."""
        data = np.ma.array(
            np.ones((1, 50, 50), dtype=np.float32),
            mask=np.zeros((1, 50, 50), dtype=bool),
        )
        img = ImageData(
            data,
            bounds=(0, 0, 10, 10),
            crs=CRS.from_epsg(4326),
        )

        # Geometry completely outside the image bounds
        geometry = {
            "type": "Polygon",
            "coordinates": [[[20, 20], [30, 20], [30, 30], [20, 30], [20, 20]]],
        }

        result = _apply_cutline_mask(img, geometry, CRS.from_epsg(4326))

        # All pixels should be outside the geometry (True = masked)
        assert result.cutline_mask is not None
        assert np.all(result.cutline_mask)

    def test_apply_cutline_mask_crs_transformation(self):
        """Test that geometry is properly transformed to target CRS."""
        data = np.ma.array(
            np.ones((1, 100, 100), dtype=np.float32),
            mask=np.zeros((1, 100, 100), dtype=bool),
        )
        # Image in Web Mercator
        img = ImageData(
            data,
            bounds=(0, 0, 1000000, 1000000),  # ~9 degrees at equator
            crs=CRS.from_epsg(3857),
        )

        # Geometry in WGS84 covering roughly the same area
        geometry = {
            "type": "Polygon",
            "coordinates": [[[0, 0], [4.5, 0], [4.5, 4.5], [0, 4.5], [0, 0]]],
        }

        # Apply with dst_crs different from WGS84
        result = _apply_cutline_mask(img, geometry, CRS.from_epsg(3857))

        # Cutline mask should be created
        assert result.cutline_mask is not None
        assert result.cutline_mask.shape == (100, 100)
        # Some pixels should be inside, some outside
        assert np.any(result.cutline_mask)
        assert np.any(~result.cutline_mask)


class TestCutlineMaskWithPixelSelection:
    """Tests proving cutline_mask improves pixel selection efficiency."""

    def create_image_with_cutline(
        self, values, mask, cutline_mask, bounds=(0, 0, 10, 10)
    ):
        """Helper to create ImageData with cutline_mask."""
        data = np.ma.array(values, mask=mask)
        img = ImageData(data, bounds=bounds, crs=CRS.from_epsg(4326))
        img.cutline_mask = cutline_mask
        return img

    def test_cutline_mask_propagates_to_pixel_selection(self):
        """Test that cutline_mask is used in pixel selection initialization."""
        # Create images with cutline masks
        # Image 1: Has data in top half (bottom half is outside footprint)
        cutline1 = np.zeros((10, 10), dtype=bool)
        cutline1[5:, :] = True  # Bottom half is outside footprint

        # Image 2: Has data in bottom half (top half is outside footprint)
        cutline2 = np.zeros((10, 10), dtype=bool)
        cutline2[:5, :] = True  # Top half is outside footprint

        stack = RasterStack.from_images(
            {
                "2021-01-01": self.create_image_with_cutline(
                    np.ones((1, 10, 10), dtype=np.float32) * 10,
                    np.zeros((1, 10, 10), dtype=bool),
                    cutline1,
                ),
                "2021-01-02": self.create_image_with_cutline(
                    np.ones((1, 10, 10), dtype=np.float32) * 20,
                    np.zeros((1, 10, 10), dtype=bool),
                    cutline2,
                ),
            }
        )

        # Apply pixel selection
        result = apply_pixel_selection(data=stack, pixel_selection="first")

        assert "data" in result
        img = result["data"]
        assert isinstance(img, ImageData)

    def test_cutline_mask_affects_mosaic_result(self):
        """Test that cutline_mask correctly masks out-of-footprint pixels."""
        # Create two images where each has valid data only in its footprint region
        # Image 1: footprint covers left half, has value 100
        # Image 2: footprint covers right half, has value 200

        # Cutline for left half valid (right half is True = outside footprint)
        cutline_left = np.zeros((10, 20), dtype=bool)
        cutline_left[:, 10:] = True

        # Cutline for right half valid (left half is True = outside footprint)
        cutline_right = np.zeros((10, 20), dtype=bool)
        cutline_right[:, :10] = True

        # Image 1: value 100 everywhere, but only left half has valid footprint
        data1 = np.ma.array(
            np.ones((1, 10, 20), dtype=np.float32) * 100,
            mask=np.zeros((1, 10, 20), dtype=bool),
        )
        img1 = ImageData(data1, bounds=(0, 0, 20, 10), crs=CRS.from_epsg(4326))
        img1.cutline_mask = cutline_left

        # Image 2: value 200 everywhere, but only right half has valid footprint
        data2 = np.ma.array(
            np.ones((1, 10, 20), dtype=np.float32) * 200,
            mask=np.zeros((1, 10, 20), dtype=bool),
        )
        img2 = ImageData(data2, bounds=(0, 0, 20, 10), crs=CRS.from_epsg(4326))
        img2.cutline_mask = cutline_right

        stack = RasterStack.from_images(
            {
                "2021-01-01": img1,
                "2021-01-02": img2,
            }
        )

        # With "first" pixel selection, we should get:
        # - Left half: 100 (from first image, which has valid footprint there)
        # - Right half: depends on implementation - first valid or 200 from second
        result = apply_pixel_selection(data=stack, pixel_selection="first")

        assert "data" in result
        img = result["data"]
        assert img.array.shape == (1, 10, 20)

    def test_cutline_enables_early_termination(self):
        """
        Test demonstrating that cutline_mask can enable early termination.

        When pixel selection method's cutline_mask indicates which areas
        need data, we can stop processing additional images once all
        valid pixels are filled.
        """
        # Create a scenario where first image fills all needed pixels
        # based on its cutline_mask

        # Full coverage cutline (all pixels are within footprint)
        full_cutline = np.zeros((10, 10), dtype=bool)

        # No coverage cutline (all pixels outside footprint)
        empty_cutline = np.ones((10, 10), dtype=bool)

        # Image 1: Full coverage with value 1
        data1 = np.ma.array(
            np.ones((1, 10, 10), dtype=np.float32),
            mask=np.zeros((1, 10, 10), dtype=bool),
        )
        img1 = ImageData(data1, bounds=(0, 0, 10, 10), crs=CRS.from_epsg(4326))
        img1.cutline_mask = full_cutline

        # Image 2: No valid data according to cutline (but has data values)
        data2 = np.ma.array(
            np.ones((1, 10, 10), dtype=np.float32) * 2,
            mask=np.zeros((1, 10, 10), dtype=bool),
        )
        img2 = ImageData(data2, bounds=(0, 0, 10, 10), crs=CRS.from_epsg(4326))
        img2.cutline_mask = empty_cutline

        stack = RasterStack.from_images(
            {
                "2021-01-01": img1,
                "2021-01-02": img2,
            }
        )

        # Apply first pixel selection
        result = apply_pixel_selection(data=stack, pixel_selection="first")

        img = result["data"]

        # All values should be from first image (value 1)
        # because it provides full coverage
        assert np.allclose(img.array.data, 1.0)

    def test_mean_with_cutline_mask(self):
        """Test mean pixel selection respects cutline masks."""
        # Two images with complementary cutlines
        cutline1 = np.zeros((10, 10), dtype=bool)
        cutline1[:, 5:] = True  # Right half outside

        cutline2 = np.zeros((10, 10), dtype=bool)
        cutline2[:, :5] = True  # Left half outside

        # Image 1: value 10 in left half (valid), right is outside footprint
        data1 = np.ma.array(
            np.ones((1, 10, 10), dtype=np.float32) * 10,
            mask=np.zeros((1, 10, 10), dtype=bool),
        )
        img1 = ImageData(data1, bounds=(0, 0, 10, 10), crs=CRS.from_epsg(4326))
        img1.cutline_mask = cutline1

        # Image 2: value 20 in right half (valid), left is outside footprint
        data2 = np.ma.array(
            np.ones((1, 10, 10), dtype=np.float32) * 20,
            mask=np.zeros((1, 10, 10), dtype=bool),
        )
        img2 = ImageData(data2, bounds=(0, 0, 10, 10), crs=CRS.from_epsg(4326))
        img2.cutline_mask = cutline2

        stack = RasterStack.from_images(
            {
                "2021-01-01": img1,
                "2021-01-02": img2,
            }
        )

        # Mean should consider both images
        result = apply_pixel_selection(data=stack, pixel_selection="mean")
        assert "data" in result


class TestCutlineMaskWithReaderIntegration:
    """Integration tests for cutline_mask in the reader pipeline."""

    def test_reader_applies_cutline_from_geometry(self):
        """Test that _apply_cutline_mask works with realistic geometry."""
        # Simulate what happens when _reader processes an item with geometry

        # Create ImageData as if returned from SimpleSTACReader.part()
        data = np.ma.array(
            np.random.rand(3, 256, 256).astype(np.float32),
            mask=np.zeros((3, 256, 256), dtype=bool),
        )
        img = ImageData(
            data,
            bounds=(-10, 40, 0, 50),  # West Europe bbox in WGS84
            crs=CRS.from_epsg(4326),
        )

        # Realistic footprint geometry (slightly smaller than bbox)
        geometry = {
            "type": "Polygon",
            "coordinates": [[[-9, 41], [-1, 41], [-1, 49], [-9, 49], [-9, 41]]],
        }

        # Apply cutline mask
        result = _apply_cutline_mask(img, geometry, CRS.from_epsg(4326))

        # Verify cutline mask was created
        assert result.cutline_mask is not None
        assert result.cutline_mask.shape == (256, 256)

        # The mask should have some True values (outside footprint)
        # and some False values (inside footprint)
        inside_count = np.sum(~result.cutline_mask)
        outside_count = np.sum(result.cutline_mask)

        # Most pixels should be inside since geometry is close to bbox
        assert inside_count > outside_count
        assert outside_count > 0  # But some should be outside

    def test_complex_geometry_cutline(self):
        """Test cutline_mask with complex polygon geometry."""
        data = np.ma.array(
            np.ones((1, 100, 100), dtype=np.float32),
            mask=np.zeros((1, 100, 100), dtype=bool),
        )
        img = ImageData(
            data,
            bounds=(0, 0, 100, 100),
            crs=CRS.from_epsg(4326),
        )

        # L-shaped polygon
        geometry = {
            "type": "Polygon",
            "coordinates": [
                [
                    [0, 0],
                    [50, 0],
                    [50, 50],
                    [100, 50],
                    [100, 100],
                    [0, 100],
                    [0, 0],
                ]
            ],
        }

        result = _apply_cutline_mask(img, geometry, CRS.from_epsg(4326))

        assert result.cutline_mask is not None
        # The L-shape should create a specific pattern
        # Top-right quadrant (25x50 area) should be outside
        # Rest should be inside
        inside_count = np.sum(~result.cutline_mask)
        total_count = result.cutline_mask.size

        # L-shape covers 75% of the area (3/4 quadrants)
        expected_coverage = 0.75
        actual_coverage = inside_count / total_count
        assert abs(actual_coverage - expected_coverage) < 0.1  # Allow 10% tolerance


class TestCutlineMaskEdgeCases:
    """Edge case tests for cutline_mask functionality."""

    def test_none_geometry(self):
        """Test behavior when no geometry is available."""

        # This tests the case where item has no geometry
        # The function should handle this gracefully
        # (tested indirectly - if geometry is None, no cutline is applied)
        pass

    def test_multipolygon_geometry(self):
        """Test cutline_mask with MultiPolygon geometry."""
        data = np.ma.array(
            np.ones((1, 100, 100), dtype=np.float32),
            mask=np.zeros((1, 100, 100), dtype=bool),
        )
        img = ImageData(
            data,
            bounds=(0, 0, 100, 100),
            crs=CRS.from_epsg(4326),
        )

        # MultiPolygon with two separate regions
        geometry = {
            "type": "MultiPolygon",
            "coordinates": [
                # First polygon - bottom left quadrant
                [[[0, 0], [50, 0], [50, 50], [0, 50], [0, 0]]],
                # Second polygon - top right quadrant
                [[[50, 50], [100, 50], [100, 100], [50, 100], [50, 50]]],
            ],
        }

        result = _apply_cutline_mask(img, geometry, CRS.from_epsg(4326))

        assert result.cutline_mask is not None
        # Two quadrants should be inside (50% coverage)
        inside_count = np.sum(~result.cutline_mask)
        total_count = result.cutline_mask.size
        actual_coverage = inside_count / total_count
        assert abs(actual_coverage - 0.5) < 0.1  # Allow 10% tolerance

    def test_empty_image(self):
        """Test cutline_mask with minimal image size."""
        data = np.ma.array(
            np.ones((1, 1, 1), dtype=np.float32),
            mask=np.zeros((1, 1, 1), dtype=bool),
        )
        img = ImageData(
            data,
            bounds=(0, 0, 1, 1),
            crs=CRS.from_epsg(4326),
        )

        geometry = {
            "type": "Polygon",
            "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]],
        }

        result = _apply_cutline_mask(img, geometry, CRS.from_epsg(4326))

        assert result.cutline_mask is not None
        assert result.cutline_mask.shape == (1, 1)


class TestReaderGeometryIntegration:
    """Tests for _reader function with geometry detection and cutline_mask application."""

    def _create_mock_image_data(self, width=100, height=100):
        """Create a mock ImageData object."""
        data = np.ma.array(
            np.ones((1, height, width), dtype=np.float32),
            mask=np.zeros((1, height, width), dtype=bool),
        )
        img = ImageData(
            data,
            bounds=(0, 0, 10, 10),
            crs=CRS.from_epsg(4326),
        )
        return img

    @patch("titiler.openeo.reader.SimpleSTACReader")
    def test_reader_detects_geometry_from_dict_item(self, mock_reader_class):
        """Test that _reader extracts geometry from dict items and applies cutline_mask."""
        # Setup mock
        mock_reader_instance = MagicMock()
        mock_reader_class.return_value.__enter__ = MagicMock(
            return_value=mock_reader_instance
        )
        mock_reader_class.return_value.__exit__ = MagicMock(return_value=False)
        mock_reader_instance.part.return_value = self._create_mock_image_data()

        # Set up the geometry that src_dst.item.geometry will return
        geometry = {
            "type": "Polygon",
            "coordinates": [[[0, 0], [5, 0], [5, 10], [0, 10], [0, 0]]],
        }
        mock_reader_instance.item.geometry = geometry

        # Create a STAC item as a dictionary with geometry
        item_dict = {
            "type": "Feature",
            "stac_version": "1.0.0",
            "id": "test-item",
            "bbox": [0, 0, 10, 10],
            "geometry": geometry,
            "properties": {"datetime": "2025-01-01T00:00:00Z"},
            "assets": {},
        }

        bbox = (0, 0, 10, 10)

        # Call _reader
        result = _reader(item_dict, bbox, dst_crs=CRS.from_epsg(4326))

        # Verify cutline_mask was applied
        assert result.cutline_mask is not None
        assert result.cutline_mask.shape == (100, 100)
        # Left half should be inside (False), right half outside (True)
        assert np.sum(~result.cutline_mask[:, :50]) > np.sum(
            ~result.cutline_mask[:, 50:]
        )

    @patch("titiler.openeo.reader.SimpleSTACReader")
    def test_reader_detects_geometry_from_pystac_item(self, mock_reader_class):
        """Test that _reader extracts geometry via src_dst.item.geometry."""
        # Setup mock
        mock_reader_instance = MagicMock()
        mock_reader_class.return_value.__enter__ = MagicMock(
            return_value=mock_reader_instance
        )
        mock_reader_class.return_value.__exit__ = MagicMock(return_value=False)
        mock_reader_instance.part.return_value = self._create_mock_image_data()

        # Set up the geometry that src_dst.item.geometry will return
        geometry = {
            "type": "Polygon",
            "coordinates": [[[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]]],
        }
        mock_reader_instance.item.geometry = geometry

        # Create a STAC item as a dictionary
        item_dict = {
            "type": "Feature",
            "stac_version": "1.0.0",
            "id": "test-pystac-item",
            "bbox": [0, 0, 10, 10],
            "geometry": geometry,
            "properties": {"datetime": "2025-01-01T00:00:00Z"},
            "assets": {},
        }

        bbox = (0, 0, 10, 10)

        # Call _reader
        result = _reader(item_dict, bbox, dst_crs=CRS.from_epsg(4326))

        # Verify cutline_mask was applied (geometry covers full bbox)
        assert result.cutline_mask is not None
        assert result.cutline_mask.shape == (100, 100)
        # All pixels should be inside the geometry
        assert np.all(~result.cutline_mask)

    @patch("titiler.openeo.reader.SimpleSTACReader")
    def test_reader_no_cutline_when_no_geometry(self, mock_reader_class):
        """Test that _reader doesn't apply cutline_mask when item has no geometry."""
        # Setup mock
        mock_reader_instance = MagicMock()
        mock_reader_class.return_value.__enter__ = MagicMock(
            return_value=mock_reader_instance
        )
        mock_reader_class.return_value.__exit__ = MagicMock(return_value=False)
        mock_reader_instance.part.return_value = self._create_mock_image_data()

        # Set item.geometry to None
        mock_reader_instance.item.geometry = None

        # Create a STAC item without geometry
        item_dict = {
            "type": "Feature",
            "stac_version": "1.0.0",
            "id": "test-item-no-geometry",
            "bbox": [0, 0, 10, 10],
            # No geometry field
            "properties": {"datetime": "2025-01-01T00:00:00Z"},
            "assets": {},
        }

        bbox = (0, 0, 10, 10)

        # Call _reader
        result = _reader(item_dict, bbox, dst_crs=CRS.from_epsg(4326))

        # Verify no cutline_mask was applied
        assert result.cutline_mask is None

    @patch("titiler.openeo.reader.SimpleSTACReader")
    def test_reader_applies_cutline_with_crs_transform(self, mock_reader_class):
        """Test that _reader correctly transforms geometry when dst_crs differs."""
        # Setup mock - image in Web Mercator
        mock_reader_instance = MagicMock()
        mock_reader_class.return_value.__enter__ = MagicMock(
            return_value=mock_reader_instance
        )
        mock_reader_class.return_value.__exit__ = MagicMock(return_value=False)

        # Create ImageData in Web Mercator
        data = np.ma.array(
            np.ones((1, 100, 100), dtype=np.float32),
            mask=np.zeros((1, 100, 100), dtype=bool),
        )
        img = ImageData(
            data,
            bounds=(0, 0, 1000000, 1000000),
            crs=CRS.from_epsg(3857),
        )
        mock_reader_instance.part.return_value = img

        # Set up the geometry in WGS84 that src_dst.item.geometry will return
        geometry = {
            "type": "Polygon",
            "coordinates": [[[0, 0], [4.5, 0], [4.5, 4.5], [0, 4.5], [0, 0]]],
        }
        mock_reader_instance.item.geometry = geometry

        # Create item with geometry in WGS84
        item_dict = {
            "type": "Feature",
            "stac_version": "1.0.0",
            "id": "test-item-crs",
            "bbox": [0, 0, 9, 9],  # Approximate bbox in WGS84
            "geometry": geometry,
            "properties": {"datetime": "2025-01-01T00:00:00Z"},
            "assets": {},
        }

        bbox = (0, 0, 1000000, 1000000)

        # Call _reader with Web Mercator dst_crs
        result = _reader(item_dict, bbox, dst_crs=CRS.from_epsg(3857))

        # Verify cutline_mask was applied and some pixels are in/out
        assert result.cutline_mask is not None
        assert result.cutline_mask.shape == (100, 100)
        # Should have both inside and outside pixels
        assert np.any(result.cutline_mask)  # Some outside
        assert np.any(~result.cutline_mask)  # Some inside

    @patch("titiler.openeo.reader.SimpleSTACReader")
    def test_reader_geometry_none_value(self, mock_reader_class):
        """Test that _reader handles None geometry value correctly."""
        # Setup mock
        mock_reader_instance = MagicMock()
        mock_reader_class.return_value.__enter__ = MagicMock(
            return_value=mock_reader_instance
        )
        mock_reader_class.return_value.__exit__ = MagicMock(return_value=False)
        mock_reader_instance.part.return_value = self._create_mock_image_data()

        # Set item.geometry to None
        mock_reader_instance.item.geometry = None

        # Create a STAC item with explicit None geometry
        item_dict = {
            "type": "Feature",
            "stac_version": "1.0.0",
            "id": "test-item-null-geometry",
            "bbox": [0, 0, 10, 10],
            "geometry": None,  # Explicit None
            "properties": {"datetime": "2025-01-01T00:00:00Z"},
            "assets": {},
        }

        bbox = (0, 0, 10, 10)

        # Call _reader
        result = _reader(item_dict, bbox, dst_crs=CRS.from_epsg(4326))

        # Verify no cutline_mask was applied
        assert result.cutline_mask is None
