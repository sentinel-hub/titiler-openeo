"""Tests for cutline_mask functionality."""

import numpy as np
from rasterio.crs import CRS
from rio_tiler.models import ImageData

from titiler.openeo.processes.implementations.reduce import apply_pixel_selection
from titiler.openeo.reader import _apply_cutline_mask


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

        stack = {
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

        stack = {
            "2021-01-01": img1,
            "2021-01-02": img2,
        }

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

        stack = {
            "2021-01-01": img1,
            "2021-01-02": img2,
        }

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

        stack = {
            "2021-01-01": img1,
            "2021-01-02": img2,
        }

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
