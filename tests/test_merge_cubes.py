"""Test merge_cubes process implementation."""

from datetime import datetime

import numpy as np
import pytest
from rio_tiler.models import ImageData

from titiler.openeo.processes.implementations.arrays import (
    OverlapResolverMissing,
    _merge_images_bands,
    _resize_image_to_match,
    merge_cubes,
)
from titiler.openeo.processes.implementations.data_model import RasterStack

# --- Helpers ---


def _make_image(
    bands: int = 2,
    height: int = 10,
    width: int = 10,
    fill: float = 1.0,
    band_names: list | None = None,
    crs: str = "EPSG:4326",
    bounds: tuple = (-180, -90, 180, 90),
) -> ImageData:
    """Create a test ImageData."""
    array = np.ma.ones((bands, height, width)) * fill
    return ImageData(
        array,
        crs=crs,
        bounds=bounds,
        band_descriptions=band_names or [],
    )


def _make_stack(
    dates: list[datetime],
    bands: int = 2,
    height: int = 10,
    width: int = 10,
    fill: float = 1.0,
    band_names: list | None = None,
) -> RasterStack:
    """Create a test RasterStack."""
    images = {}
    for i, dt in enumerate(dates):
        images[dt] = _make_image(
            bands=bands,
            height=height,
            width=width,
            fill=fill + i,
            band_names=band_names,
        )
    return RasterStack.from_images(images)


# --- Overlap resolvers ---


def add_resolver(x, y, context=None):
    """Overlap resolver that adds values."""
    return x + y


def multiply_resolver(x, y, context=None):
    """Overlap resolver that multiplies values."""
    return x * y


def pick_x_resolver(x, y, context=None):
    """Overlap resolver that returns x (cube1)."""
    return x


def pick_y_resolver(x, y, context=None):
    """Overlap resolver that returns y (cube2)."""
    return y


def context_resolver(x, y, context=None):
    """Overlap resolver that uses context."""
    factor = context.get("factor", 1.0) if context else 1.0
    return (x + y) * factor


# =============================================================================
# Test _resize_image_to_match
# =============================================================================


class TestResizeImageToMatch:
    """Test _resize_image_to_match helper."""

    def test_no_resize_needed(self):
        """Image already matching target dims returns same object."""
        img = _make_image(height=10, width=10)
        result = _resize_image_to_match(img, 10, 10)
        assert result is img  # Same object, no copy

    def test_resize_smaller(self):
        """Larger image is resized down to target dims."""
        img = _make_image(height=20, width=20, fill=5.0)
        result = _resize_image_to_match(img, 10, 10)
        assert result.height == 10
        assert result.width == 10
        assert result.array.shape == (2, 10, 10)

    def test_resize_larger(self):
        """Smaller image is resized up to target dims."""
        img = _make_image(height=5, width=5, fill=3.0)
        result = _resize_image_to_match(img, 10, 10)
        assert result.height == 10
        assert result.width == 10

    def test_resize_preserves_metadata(self):
        """Resize preserves band names, CRS and bounds."""
        img = _make_image(height=20, width=20, band_names=["B1", "B2"], fill=1.0)
        result = _resize_image_to_match(img, 10, 10)
        assert result.band_descriptions == ["B1", "B2"]
        assert result.crs == img.crs
        assert result.bounds == img.bounds


# =============================================================================
# Test _merge_images_bands
# =============================================================================


class TestMergeImagesBands:
    def test_disjoint_bands(self):
        """Bands are disjoint → concatenate without resolver."""
        img1 = _make_image(bands=2, fill=1.0, band_names=["B1", "B2"])
        img2 = _make_image(bands=2, fill=2.0, band_names=["B3", "B4"])

        result = _merge_images_bands(img1, img2, overlap_resolver=None)

        assert result.count == 4
        assert result.band_descriptions == ["B1", "B2", "B3", "B4"]
        np.testing.assert_array_equal(result.array[0], 1.0)  # B1 from cube1
        np.testing.assert_array_equal(result.array[1], 1.0)  # B2 from cube1
        np.testing.assert_array_equal(result.array[2], 2.0)  # B3 from cube2
        np.testing.assert_array_equal(result.array[3], 2.0)  # B4 from cube2

    def test_overlapping_bands_with_resolver(self):
        """Overlapping bands resolved with add_resolver."""
        img1 = _make_image(bands=2, fill=1.0, band_names=["B1", "B2"])
        img2 = _make_image(bands=2, fill=3.0, band_names=["B2", "B3"])

        result = _merge_images_bands(img1, img2, overlap_resolver=add_resolver)

        assert result.count == 3
        assert result.band_descriptions == ["B1", "B2", "B3"]
        np.testing.assert_array_equal(result.array[0], 1.0)  # B1 from cube1
        np.testing.assert_array_equal(result.array[1], 4.0)  # B2: 1+3
        np.testing.assert_array_equal(result.array[2], 3.0)  # B3 from cube2

    def test_overlapping_bands_missing_resolver(self):
        """Overlapping bands without resolver → OverlapResolverMissing."""
        img1 = _make_image(bands=2, fill=1.0, band_names=["B1", "B2"])
        img2 = _make_image(bands=2, fill=3.0, band_names=["B2", "B3"])

        with pytest.raises(OverlapResolverMissing):
            _merge_images_bands(img1, img2, overlap_resolver=None)

    def test_no_band_names_requires_resolver(self):
        """Both images without band names → all data overlaps → needs resolver."""
        img1 = _make_image(bands=2, fill=1.0, band_names=[])
        img2 = _make_image(bands=2, fill=2.0, band_names=[])

        with pytest.raises(OverlapResolverMissing):
            _merge_images_bands(img1, img2, overlap_resolver=None)

    def test_no_band_names_with_resolver(self):
        """Both images without band names → resolver applied."""
        img1 = _make_image(bands=2, fill=1.0)
        img2 = _make_image(bands=2, fill=2.0)

        result = _merge_images_bands(img1, img2, overlap_resolver=add_resolver)

        assert result.count == 2
        np.testing.assert_array_equal(result.array[0], 3.0)  # 1+2

    def test_fully_overlapping_bands(self):
        """All bands overlap → resolver for all."""
        img1 = _make_image(bands=2, fill=10.0, band_names=["B1", "B2"])
        img2 = _make_image(bands=2, fill=5.0, band_names=["B1", "B2"])

        result = _merge_images_bands(img1, img2, overlap_resolver=multiply_resolver)

        assert result.count == 2
        assert result.band_descriptions == ["B1", "B2"]
        np.testing.assert_array_equal(result.array[0], 50.0)  # 10*5
        np.testing.assert_array_equal(result.array[1], 50.0)  # 10*5

    def test_context_passed_to_resolver(self):
        """Context is forwarded to the overlap resolver."""
        img1 = _make_image(bands=1, fill=1.0, band_names=["B1"])
        img2 = _make_image(bands=1, fill=2.0, band_names=["B1"])

        result = _merge_images_bands(
            img1, img2, overlap_resolver=context_resolver, context={"factor": 10.0}
        )

        # (1+2)*10 = 30
        np.testing.assert_array_equal(result.array[0], 30.0)


# =============================================================================
# Test merge_cubes
# =============================================================================


class TestMergeCubes:
    """Test the merge_cubes process."""

    def test_disjoint_timestamps(self):
        """Two cubes with no temporal overlap → simple merge."""
        dates1 = [datetime(2021, 1, 1), datetime(2021, 1, 2)]
        dates2 = [datetime(2021, 1, 3), datetime(2021, 1, 4)]

        cube1 = _make_stack(dates1, fill=1.0, band_names=["B1"])
        cube2 = _make_stack(dates2, fill=5.0, band_names=["B1"])

        result = merge_cubes(cube1=cube1, cube2=cube2)

        assert isinstance(result, RasterStack)
        assert len(result) == 4
        # Keys should be sorted
        result_keys = list(result.keys())
        assert result_keys == sorted(dates1 + dates2)

    def test_disjoint_bands_same_timestamps(self):
        """Same timestamps, disjoint bands → concatenate bands."""
        dates = [datetime(2021, 1, 1), datetime(2021, 1, 2)]

        cube1 = _make_stack(dates, bands=2, fill=1.0, band_names=["B1", "B2"])
        cube2 = _make_stack(dates, bands=2, fill=5.0, band_names=["B3", "B4"])

        result = merge_cubes(cube1=cube1, cube2=cube2)

        assert len(result) == 2
        for key in dates:
            img = result[key]
            assert img.count == 4
            assert img.band_descriptions == ["B1", "B2", "B3", "B4"]

    def test_overlapping_bands_with_resolver(self):
        """Same timestamps, overlapping bands → resolver applied."""
        dt = datetime(2021, 1, 1)

        cube1 = RasterStack.from_images(
            {dt: _make_image(bands=2, fill=2.0, band_names=["B1", "B2"])}
        )
        cube2 = RasterStack.from_images(
            {dt: _make_image(bands=2, fill=3.0, band_names=["B2", "B3"])}
        )

        result = merge_cubes(cube1=cube1, cube2=cube2, overlap_resolver=add_resolver)

        assert len(result) == 1
        img = result[dt]
        assert img.count == 3
        assert img.band_descriptions == ["B1", "B2", "B3"]
        np.testing.assert_array_equal(img.array[0], 2.0)  # B1
        np.testing.assert_array_equal(img.array[1], 5.0)  # B2: 2+3
        np.testing.assert_array_equal(img.array[2], 3.0)  # B3

    def test_overlapping_bands_missing_resolver_raises(self):
        """Same timestamps, overlapping bands, no resolver → error."""
        dt = datetime(2021, 1, 1)

        cube1 = RasterStack.from_images(
            {dt: _make_image(bands=2, fill=1.0, band_names=["B1", "B2"])}
        )
        cube2 = RasterStack.from_images(
            {dt: _make_image(bands=2, fill=2.0, band_names=["B2", "B3"])}
        )

        with pytest.raises(OverlapResolverMissing):
            merge_cubes(cube1=cube1, cube2=cube2)

    def test_partial_temporal_overlap(self):
        """Some timestamps overlap, some don't."""
        dt_shared = datetime(2021, 1, 2)
        dates1 = [datetime(2021, 1, 1), dt_shared]
        dates2 = [dt_shared, datetime(2021, 1, 3)]

        cube1 = _make_stack(dates1, bands=1, fill=1.0, band_names=["B1"])
        cube2 = _make_stack(dates2, bands=1, fill=10.0, band_names=["B2"])

        result = merge_cubes(cube1=cube1, cube2=cube2)

        assert len(result) == 3
        # Non-overlapping: keep as-is
        assert result[datetime(2021, 1, 1)].count == 1
        assert result[datetime(2021, 1, 3)].count == 1
        # Overlapping timestamp with disjoint bands: concatenated
        merged_img = result[dt_shared]
        assert merged_img.count == 2
        assert merged_img.band_descriptions == ["B1", "B2"]

    def test_empty_cube1(self):
        """cube1 is empty → return cube2."""
        cube2 = _make_stack([datetime(2021, 1, 1)], fill=1.0, band_names=["B1"])
        result = merge_cubes(cube1={}, cube2=cube2)
        assert result is cube2

    def test_empty_cube2(self):
        """cube2 is empty → return cube1."""
        cube1 = _make_stack([datetime(2021, 1, 1)], fill=1.0, band_names=["B1"])
        result = merge_cubes(cube1=cube1, cube2={})
        assert result is cube1

    def test_both_empty(self):
        """Both cubes empty → return empty."""
        result = merge_cubes(cube1={}, cube2={})
        assert not result

    def test_different_spatial_dimensions(self):
        """cube2 has different spatial dims → resized to match cube1."""
        dt = datetime(2021, 1, 1)

        cube1 = RasterStack.from_images(
            {dt: _make_image(bands=1, height=10, width=10, fill=1.0, band_names=["B1"])}
        )
        cube2 = RasterStack.from_images(
            {dt: _make_image(bands=1, height=20, width=20, fill=2.0, band_names=["B2"])}
        )

        result = merge_cubes(cube1=cube1, cube2=cube2)

        img = result[dt]
        assert img.height == 10
        assert img.width == 10
        assert img.count == 2

    def test_multi_sensor_fusion(self):
        """Simulate Sentinel-1 + Sentinel-2 fusion with different bands."""
        s1_dates = [datetime(2021, 1, 1), datetime(2021, 1, 3)]
        s2_dates = [datetime(2021, 1, 2), datetime(2021, 1, 3)]

        # Sentinel-1: VV, VH bands
        s1_cube = _make_stack(s1_dates, bands=2, fill=0.5, band_names=["VV", "VH"])
        # Sentinel-2: B04, B08 bands
        s2_cube = _make_stack(s2_dates, bands=2, fill=0.3, band_names=["B04", "B08"])

        result = merge_cubes(cube1=s1_cube, cube2=s2_cube)

        assert len(result) == 3

        # Jan 1: only S1
        img_jan1 = result[datetime(2021, 1, 1)]
        assert img_jan1.band_descriptions == ["VV", "VH"]

        # Jan 2: only S2
        img_jan2 = result[datetime(2021, 1, 2)]
        assert img_jan2.band_descriptions == ["B04", "B08"]

        # Jan 3: merged (disjoint bands)
        img_jan3 = result[datetime(2021, 1, 3)]
        assert img_jan3.count == 4
        assert img_jan3.band_descriptions == ["VV", "VH", "B04", "B08"]

    def test_context_passed_through(self):
        """Context is forwarded to overlap resolver."""
        dt = datetime(2021, 1, 1)

        cube1 = RasterStack.from_images(
            {dt: _make_image(bands=1, fill=1.0, band_names=["B1"])}
        )
        cube2 = RasterStack.from_images(
            {dt: _make_image(bands=1, fill=2.0, band_names=["B1"])}
        )

        result = merge_cubes(
            cube1=cube1,
            cube2=cube2,
            overlap_resolver=context_resolver,
            context={"factor": 5.0},
        )

        img = result[dt]
        # (1+2)*5 = 15
        np.testing.assert_array_equal(img.array[0], 15.0)

    def test_temporal_ordering_preserved(self):
        """Result timestamps are sorted even if inputs are unordered."""
        dt1 = datetime(2021, 3, 1)
        dt2 = datetime(2021, 1, 1)
        dt3 = datetime(2021, 2, 1)

        cube1 = RasterStack.from_images({dt1: _make_image(fill=1.0, band_names=["B1"])})
        cube2 = RasterStack.from_images(
            {
                dt2: _make_image(fill=2.0, band_names=["B2"]),
                dt3: _make_image(fill=3.0, band_names=["B3"]),
            }
        )

        result = merge_cubes(cube1=cube1, cube2=cube2)
        result_keys = list(result.keys())
        assert result_keys == [dt2, dt3, dt1]  # sorted

    def test_pick_x_resolver(self):
        """Overlap resolver that picks cube1 values."""
        dt = datetime(2021, 1, 1)

        cube1 = RasterStack.from_images(
            {dt: _make_image(bands=1, fill=100.0, band_names=["B1"])}
        )
        cube2 = RasterStack.from_images(
            {dt: _make_image(bands=1, fill=999.0, band_names=["B1"])}
        )

        result = merge_cubes(cube1=cube1, cube2=cube2, overlap_resolver=pick_x_resolver)

        np.testing.assert_array_equal(result[dt].array[0], 100.0)

    def test_pick_y_resolver(self):
        """Overlap resolver that picks cube2 values."""
        dt = datetime(2021, 1, 1)

        cube1 = RasterStack.from_images(
            {dt: _make_image(bands=1, fill=100.0, band_names=["B1"])}
        )
        cube2 = RasterStack.from_images(
            {dt: _make_image(bands=1, fill=999.0, band_names=["B1"])}
        )

        result = merge_cubes(cube1=cube1, cube2=cube2, overlap_resolver=pick_y_resolver)

        np.testing.assert_array_equal(result[dt].array[0], 999.0)

    def test_masked_data_preserved(self):
        """Masked (nodata) values are preserved through merge."""
        dt = datetime(2021, 1, 1)

        data1 = np.ma.ones((1, 10, 10))
        data1[0, 0:5, :] = np.ma.masked
        img1 = ImageData(
            data1,
            crs="EPSG:4326",
            bounds=(-180, -90, 180, 90),
            band_descriptions=["B1"],
        )

        data2 = np.ma.ones((1, 10, 10)) * 2.0
        img2 = ImageData(
            data2,
            crs="EPSG:4326",
            bounds=(-180, -90, 180, 90),
            band_descriptions=["B2"],
        )

        cube1 = RasterStack.from_images({dt: img1})
        cube2 = RasterStack.from_images({dt: img2})

        result = merge_cubes(cube1=cube1, cube2=cube2)

        merged = result[dt]
        assert merged.count == 2
        # B1 mask should be preserved
        assert np.ma.is_masked(merged.array[0])
        assert merged.array[0, 0, 0] is np.ma.masked
        # B2 should not be masked
        assert (
            not np.any(merged.array[1].mask)
            if isinstance(merged.array[1].mask, np.ndarray)
            else True
        )

    def test_single_timestamp_merge_bands(self):
        """Spec example 1: same x,y,t, different bands - no resolver needed."""
        dt = datetime(2021, 6, 15)

        cube1 = RasterStack.from_images(
            {dt: _make_image(bands=2, fill=1.0, band_names=["B1", "B2"])}
        )
        cube2 = RasterStack.from_images(
            {dt: _make_image(bands=2, fill=2.0, band_names=["B3", "B4"])}
        )

        result = merge_cubes(cube1=cube1, cube2=cube2)

        img = result[dt]
        assert img.count == 4
        assert img.band_descriptions == ["B1", "B2", "B3", "B4"]
