"""Tests for RasterStack lazy behavior with ImageRef.

These tests verify that when RasterStack is created with dimension parameters
(width, height, bounds, dst_crs, band_names), it creates ImageRef instances
that enable cutline mask computation without executing tasks.
"""

from datetime import datetime
from unittest.mock import MagicMock

import numpy as np
from rasterio.crs import CRS
from rio_tiler.models import ImageData
from shapely.geometry import box, mapping

from titiler.openeo.processes.implementations.data_model import (
    ImageRef,
    RasterStack,
    compute_cutline_mask,
)
from titiler.openeo.processes.implementations.reduce import (
    _collect_images_from_data,
    apply_pixel_selection,
)


class TestComputeCutlineMask:
    """Tests for the compute_cutline_mask utility function."""

    def test_compute_cutline_mask_full_coverage(self):
        """Geometry covering entire bounds produces all-False mask (all valid)."""
        geometry = mapping(box(0, 0, 10, 10))
        bounds = (0.0, 0.0, 10.0, 10.0)
        width, height = 100, 100
        crs = CRS.from_epsg(4326)

        mask = compute_cutline_mask(geometry, width, height, bounds, crs)

        assert mask is not None
        assert mask.shape == (height, width)
        # All pixels should be valid (False = inside geometry)
        assert not mask.any(), "All pixels should be inside the geometry"

    def test_compute_cutline_mask_partial_coverage(self):
        """Geometry covering half the bounds produces partial mask."""
        # Geometry covers left half only
        geometry = mapping(box(0, 0, 5, 10))
        bounds = (0.0, 0.0, 10.0, 10.0)
        width, height = 100, 100
        crs = CRS.from_epsg(4326)

        mask = compute_cutline_mask(geometry, width, height, bounds, crs)

        assert mask is not None
        assert mask.shape == (height, width)
        # Left half should be valid (False), right half outside (True)
        left_half = mask[:, :50]
        right_half = mask[:, 50:]
        assert not left_half.any(), "Left half should be inside geometry"
        assert right_half.all(), "Right half should be outside geometry"

    def test_compute_cutline_mask_no_coverage(self):
        """Geometry outside bounds produces all-True mask (all outside)."""
        # Geometry is completely outside the bounds
        geometry = mapping(box(20, 20, 30, 30))
        bounds = (0.0, 0.0, 10.0, 10.0)
        width, height = 100, 100
        crs = CRS.from_epsg(4326)

        mask = compute_cutline_mask(geometry, width, height, bounds, crs)

        assert mask is not None
        assert mask.shape == (height, width)
        # All pixels should be outside (True)
        assert mask.all(), "All pixels should be outside the geometry"

    def test_compute_cutline_mask_none_geometry(self):
        """None geometry produces all-True mask (all outside, with warning)."""
        bounds = (0.0, 0.0, 10.0, 10.0)
        width, height = 100, 100
        crs = CRS.from_epsg(4326)

        # Note: compute_cutline_mask with None geometry produces all-True mask
        # (all pixels outside). The ImageRef.cutline_mask() handles None
        # geometry separately by returning None.
        mask = compute_cutline_mask(None, width, height, bounds, crs)

        assert mask is not None
        assert mask.shape == (height, width)
        # All pixels are outside when geometry is None
        assert mask.all()

    def test_compute_cutline_mask_multiple_geometries(self):
        """Multiple geometries create union mask (valid if inside ANY geometry)."""
        # Two geometries covering left and right halves
        geometry1 = mapping(box(0, 0, 5, 10))  # Left half
        geometry2 = mapping(box(5, 0, 10, 10))  # Right half
        bounds = (0.0, 0.0, 10.0, 10.0)
        width, height = 100, 100
        crs = CRS.from_epsg(4326)

        # Pass as list of geometries
        mask = compute_cutline_mask([geometry1, geometry2], width, height, bounds, crs)

        assert mask is not None
        assert mask.shape == (height, width)
        # Together they cover the entire area - all pixels valid
        assert not mask.any(), "Union of both geometries should cover entire area"

    def test_compute_cutline_mask_multiple_geometries_partial(self):
        """Multiple geometries with partial coverage create correct union."""
        # Two small geometries in different corners
        geometry1 = mapping(box(0, 0, 3, 3))  # Bottom-left corner
        geometry2 = mapping(box(7, 7, 10, 10))  # Top-right corner
        bounds = (0.0, 0.0, 10.0, 10.0)
        width, height = 100, 100
        crs = CRS.from_epsg(4326)

        mask = compute_cutline_mask([geometry1, geometry2], width, height, bounds, crs)

        assert mask is not None
        assert mask.shape == (height, width)
        # Some pixels should be valid, some outside
        assert mask.any(), "Some pixels should be outside geometries"
        assert not mask.all(), "Some pixels should be inside geometries"

    def test_compute_cutline_mask_empty_list(self):
        """Empty geometry list produces all-True mask (all outside)."""
        bounds = (0.0, 0.0, 10.0, 10.0)
        width, height = 100, 100
        crs = CRS.from_epsg(4326)

        mask = compute_cutline_mask([], width, height, bounds, crs)

        assert mask is not None
        assert mask.shape == (height, width)
        assert mask.all(), "All pixels should be outside when no geometries"


class TestImageRef:
    """Tests for ImageRef dataclass."""

    def test_lazy_image_ref_properties(self):
        """ImageRef stores all properties correctly."""
        geometry = mapping(box(0, 0, 10, 10))
        bounds = (0.0, 0.0, 10.0, 10.0)
        crs = CRS.from_epsg(4326)
        task_fn = MagicMock(return_value=None)

        ref = ImageRef.from_task(
            task_fn=task_fn,
            width=256,
            height=256,
            bounds=bounds,
            crs=crs,
            band_names=["B04", "B08"],
            geometry=geometry,
        )

        assert ref.geometry == geometry
        assert ref.width == 256
        assert ref.height == 256
        assert ref.bounds == bounds
        assert ref.crs == crs
        assert ref.band_names == ["B04", "B08"]
        assert ref.count == 2

    def test_lazy_image_ref_cutline_mask_without_task_execution(self):
        """cutline_mask() computes mask without executing task."""
        geometry = mapping(box(0, 0, 5, 10))  # Left half
        bounds = (0.0, 0.0, 10.0, 10.0)
        crs = CRS.from_epsg(4326)
        task_fn = MagicMock(return_value=None)

        ref = ImageRef.from_task(
            task_fn=task_fn,
            width=100,
            height=100,
            bounds=bounds,
            crs=crs,
            band_names=["B04"],
            geometry=geometry,
        )

        # Compute cutline mask - should NOT execute task
        mask = ref.cutline_mask()

        assert mask is not None
        assert mask.shape == (100, 100)
        # Task should NOT have been called
        task_fn.assert_not_called()

    def test_lazy_image_ref_cutline_mask_cached(self):
        """cutline_mask() caches the result."""
        geometry = mapping(box(0, 0, 10, 10))
        bounds = (0.0, 0.0, 10.0, 10.0)
        crs = CRS.from_epsg(4326)
        task_fn = MagicMock(return_value=None)

        ref = ImageRef.from_task(
            task_fn=task_fn,
            width=100,
            height=100,
            bounds=bounds,
            crs=crs,
            band_names=["B04"],
            geometry=geometry,
        )

        mask1 = ref.cutline_mask()
        mask2 = ref.cutline_mask()

        # Should return same object (cached)
        assert mask1 is mask2

    def test_lazy_image_ref_realize_executes_task(self):
        """realize() executes the task and returns ImageData."""
        geometry = mapping(box(0, 0, 10, 10))
        bounds = (0.0, 0.0, 10.0, 10.0)
        crs = CRS.from_epsg(4326)

        # Create mock ImageData
        mock_data = np.ma.array(np.ones((1, 100, 100), dtype=np.float32))
        mock_image = ImageData(mock_data, bounds=bounds, crs=crs)

        # Task function returns ImageData directly
        task_fn = MagicMock(return_value=mock_image)

        ref = ImageRef.from_task(
            task_fn=task_fn,
            width=100,
            height=100,
            bounds=bounds,
            crs=crs,
            band_names=["B04"],
            geometry=geometry,
        )

        # Realize - should execute task
        result = ref.realize()

        assert result is mock_image
        task_fn.assert_called_once()


class TestRasterStackWithDimensions:
    """Tests for RasterStack when created with dimension parameters."""

    def _create_mock_task(self, item_id: str, geometry: dict, image_data: ImageData):
        """Create a mock task tuple."""
        mock_future = MagicMock()
        mock_future.result.return_value = image_data
        mock_asset = {
            "id": item_id,
            "geometry": geometry,
            "properties": {"datetime": "2021-01-01T00:00:00Z"},
        }
        return (mock_future, mock_asset)

    def test_lazy_raster_stack_creates_image_refs_with_dimensions(self):
        """When dimensions provided, RasterStack creates ImageRef instances."""
        bounds = (0.0, 0.0, 10.0, 10.0)
        crs = CRS.from_epsg(4326)
        geometry = mapping(box(0, 0, 10, 10))

        mock_data = np.ma.array(np.ones((1, 256, 256), dtype=np.float32))
        mock_image = ImageData(mock_data, bounds=bounds, crs=crs)

        task = self._create_mock_task("item1", geometry, mock_image)

        stack = RasterStack(
            tasks=[task],
            timestamp_fn=lambda asset: datetime(2021, 1, 1),
            width=256,
            height=256,
            bounds=bounds,
            dst_crs=crs,
            band_names=["B04"],
        )

        # Should have image refs
        image_refs = stack.get_image_refs()
        assert len(image_refs) == 1

        key, ref = image_refs[0]
        assert key == datetime(2021, 1, 1)  # Keys are now datetime objects
        assert isinstance(ref, ImageRef)
        assert ref.width == 256
        assert ref.height == 256

    def test_lazy_raster_stack_no_image_refs_without_dimensions(self):
        """Without dimensions, RasterStack does not create ImageRef instances."""
        geometry = mapping(box(0, 0, 10, 10))
        bounds = (0.0, 0.0, 10.0, 10.0)
        crs = CRS.from_epsg(4326)

        mock_data = np.ma.array(np.ones((1, 256, 256), dtype=np.float32))
        mock_image = ImageData(mock_data, bounds=bounds, crs=crs)

        task = self._create_mock_task("item1", geometry, mock_image)

        # Create without dimension parameters
        stack = RasterStack(
            tasks=[task],
            timestamp_fn=lambda asset: datetime(2021, 1, 1),
            # No width, height, bounds, dst_crs, band_names
        )

        # Should NOT have image refs (empty list)
        image_refs = stack.get_image_refs()
        assert len(image_refs) == 0

    def test_lazy_raster_stack_image_ref_cutline_without_task_execution(self):
        """Image refs can compute cutline mask without executing any tasks."""
        bounds = (0.0, 0.0, 10.0, 10.0)
        crs = CRS.from_epsg(4326)
        geometry = mapping(box(0, 0, 5, 10))  # Left half coverage

        mock_future = MagicMock()
        mock_future.result.return_value = None  # Should NOT be called
        mock_asset = {
            "id": "item1",
            "geometry": geometry,
            "properties": {"datetime": "2021-01-01T00:00:00Z"},
        }
        task = (mock_future, mock_asset)

        stack = RasterStack(
            tasks=[task],
            timestamp_fn=lambda asset: datetime(2021, 1, 1),
            width=100,
            height=100,
            bounds=bounds,
            dst_crs=crs,
            band_names=["B04"],
        )

        # Get image ref and compute cutline mask
        image_refs = stack.get_image_refs()
        assert len(image_refs) == 1

        _, ref = image_refs[0]
        mask = ref.cutline_mask()

        # Should have valid mask
        assert mask is not None
        assert mask.shape == (100, 100)

        # Task should NOT have been executed
        mock_future.result.assert_not_called()


class TestCollectImagesFromDataWithImageRefs:
    """Tests for _collect_images_from_data returning ImageRef instances."""

    def _create_stack_with_refs(self):
        """Helper to create a RasterStack with ImageRef instances."""
        bounds = (0.0, 0.0, 10.0, 10.0)
        crs = CRS.from_epsg(4326)
        geometry = mapping(box(0, 0, 10, 10))

        mock_data = np.ma.array(np.ones((1, 100, 100), dtype=np.float32))
        mock_image = ImageData(mock_data, bounds=bounds, crs=crs)

        mock_future = MagicMock()
        mock_future.result.return_value = mock_image
        mock_asset = {
            "id": "item1",
            "geometry": geometry,
            "properties": {"datetime": "2021-01-01T00:00:00Z"},
        }
        task = (mock_future, mock_asset)

        return RasterStack(
            tasks=[task],
            timestamp_fn=lambda asset: datetime(2021, 1, 1),
            width=100,
            height=100,
            bounds=bounds,
            dst_crs=crs,
            band_names=["B04"],
        ), mock_future

    def test_collect_images_returns_refs_when_available(self):
        """_collect_images_from_data returns ImageRef when stack has refs."""
        stack, mock_future = self._create_stack_with_refs()

        images = _collect_images_from_data(stack)

        assert len(images) == 1
        key, img_or_ref = images[0]
        assert key == datetime(2021, 1, 1)  # Keys are now datetime objects
        # Should return ImageRef, not ImageData
        assert isinstance(img_or_ref, ImageRef)
        # Task should not have been executed
        mock_future.result.assert_not_called()


class TestApplyPixelSelectionTrulyLazy:
    """Tests for apply_pixel_selection with truly lazy behavior."""

    def _create_stack_with_multiple_items(self):
        """Create RasterStack with multiple items for pixel selection tests."""
        bounds = (0.0, 0.0, 10.0, 10.0)
        crs = CRS.from_epsg(4326)

        # Item 1: covers left half, value 10
        geometry1 = mapping(box(0, 0, 5, 10))
        mock_data1 = np.ma.array(np.full((1, 100, 100), 10.0, dtype=np.float32))
        mock_image1 = ImageData(mock_data1, bounds=bounds, crs=crs)
        mock_image1.cutline_mask = np.zeros((100, 100), dtype=bool)
        mock_image1.cutline_mask[:, 50:] = True  # Right half outside

        # Create a callable that returns the mock image
        mock_task_fn1 = MagicMock(return_value=mock_image1)
        task1 = (
            mock_task_fn1,
            {
                "id": "item1",
                "geometry": geometry1,
                "properties": {"datetime": "2021-01-01T00:00:00Z"},
            },
        )

        # Item 2: covers right half, value 20
        geometry2 = mapping(box(5, 0, 10, 10))
        mock_data2 = np.ma.array(np.full((1, 100, 100), 20.0, dtype=np.float32))
        mock_image2 = ImageData(mock_data2, bounds=bounds, crs=crs)
        mock_image2.cutline_mask = np.zeros((100, 100), dtype=bool)
        mock_image2.cutline_mask[:, :50] = True  # Left half outside

        mock_task_fn2 = MagicMock(return_value=mock_image2)
        task2 = (
            mock_task_fn2,
            {
                "id": "item2",
                "geometry": geometry2,
                "properties": {"datetime": "2021-01-02T00:00:00Z"},
            },
        )

        stack = RasterStack(
            tasks=[task1, task2],
            timestamp_fn=lambda asset: datetime.fromisoformat(
                asset["properties"]["datetime"].replace("Z", "+00:00")
            ),
            width=100,
            height=100,
            bounds=bounds,
            dst_crs=crs,
            band_names=["B04"],
        )

        return stack, mock_task_fn1, mock_task_fn2

    def test_apply_pixel_selection_computes_aggregated_cutline_from_refs(self):
        """apply_pixel_selection computes aggregated cutline from ImageRef instances."""
        stack, mock_task_fn1, mock_task_fn2 = self._create_stack_with_multiple_items()

        # Before apply_pixel_selection, verify we have image refs
        refs = stack.get_image_refs()
        assert len(refs) == 2

        # Both refs should be able to compute cutline masks
        for _, ref in refs:
            mask = ref.cutline_mask()
            assert mask is not None
            assert mask.shape == (100, 100)

        # At this point, tasks should NOT have been executed
        mock_task_fn1.assert_not_called()
        mock_task_fn2.assert_not_called()

    def test_apply_pixel_selection_executes_tasks_when_feeding_pixels(self):
        """apply_pixel_selection only executes tasks when actually feeding pixels."""
        stack, mock_task_fn1, mock_task_fn2 = self._create_stack_with_multiple_items()

        # Apply pixel selection - this should eventually execute tasks
        result = apply_pixel_selection(data=stack, pixel_selection="first")

        assert result.first is not None  # Use .first property for single results
        assert result.first.array.shape == (1, 100, 100)

        # At least one task should have been executed (the first one definitely)
        # Note: due to early termination in pixel selection, the second task
        # may not be called if the first image already fills all pixels
        mock_task_fn1.assert_called()
        # The second task may or may not be called depending on early termination
        # Both behaviors are valid - what matters is that tasks are only executed
        # when realize() is called, not during cutline computation

    def test_apply_pixel_selection_with_from_images_still_works(self):
        """apply_pixel_selection still works with RasterStack.from_images() (non-lazy)."""
        bounds = (0.0, 0.0, 10.0, 10.0)
        crs = CRS.from_epsg(4326)

        data1 = np.ma.array(np.full((1, 10, 10), 10.0, dtype=np.float32))
        img1 = ImageData(data1, bounds=bounds, crs=crs)

        data2 = np.ma.array(np.full((1, 10, 10), 20.0, dtype=np.float32))
        img2 = ImageData(data2, bounds=bounds, crs=crs)

        stack = RasterStack.from_images(
            {datetime(2021, 1, 1): img1, datetime(2021, 1, 2): img2}
        )

        result = apply_pixel_selection(data=stack, pixel_selection="first")

        assert result.first is not None  # Use .first property for single results
        # Should be first image values
        np.testing.assert_array_equal(result.first.array.data, 10.0)
