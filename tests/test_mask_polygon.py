"""Tests for mask_polygon process."""

from datetime import datetime

import numpy as np
import pytest
from rasterio.crs import CRS
from rio_tiler.models import ImageData

from titiler.openeo.processes.implementations.data_model import RasterStack
from titiler.openeo.processes.implementations.spatial import (
    _extract_geometries_from_mask,
    _rasterize_geometries,
    mask_polygon,
)

# Shared test fixtures

EPSG_4326 = CRS.from_epsg(4326)

POLYGON = {
    "type": "Polygon",
    "coordinates": [[[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0], [0.0, 0.0]]],
}

MULTIPOLYGON = {
    "type": "MultiPolygon",
    "coordinates": [
        [[[0.0, 0.0], [0.5, 0.0], [0.5, 0.5], [0.0, 0.5], [0.0, 0.0]]],
        [[[0.5, 0.5], [1.0, 0.5], [1.0, 1.0], [0.5, 1.0], [0.5, 0.5]]],
    ],
}

FEATURE = {
    "type": "Feature",
    "geometry": POLYGON,
    "properties": {"name": "test"},
}

FEATURE_COLLECTION = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "geometry": POLYGON,
            "properties": {"name": "poly1"},
        },
    ],
}


def _make_stack(
    data: np.ndarray,
    bounds=(0.0, 0.0, 2.0, 2.0),
    crs=EPSG_4326,
    band_names=None,
    nodata=None,
) -> RasterStack:
    """Create a RasterStack with a single timestamp from a numpy array.

    Args:
        data: 3D array (bands, height, width).
        bounds: Spatial bounds.
        crs: Coordinate reference system.
        band_names: Band names.
        nodata: If set, positions where data == nodata are masked.
    """
    if band_names is None:
        band_names = [f"b{i}" for i in range(data.shape[0])]

    if nodata is not None:
        mask = data == nodata
        masked = np.ma.MaskedArray(data, mask=mask)
    else:
        masked = np.ma.MaskedArray(data, mask=False)

    img = ImageData(
        masked,
        crs=crs,
        bounds=bounds,
        band_names=band_names,
    )
    return RasterStack.from_images({datetime(2024, 1, 1): img})


# ---------------------------------------------------------------------------
# Tests for _extract_geometries_from_mask
# ---------------------------------------------------------------------------


class TestExtractGeometriesFromMask:
    """Tests for _extract_geometries_from_mask helper."""

    def test_polygon_geometry(self):
        """Extract single geometry from a Polygon."""
        result = _extract_geometries_from_mask(POLYGON)
        assert len(result) == 1
        assert result[0]["type"] == "Polygon"

    def test_multipolygon_geometry(self):
        """Extract single geometry from a MultiPolygon."""
        result = _extract_geometries_from_mask(MULTIPOLYGON)
        assert len(result) == 1
        assert result[0]["type"] == "MultiPolygon"

    def test_feature(self):
        """Extract geometry from a Feature."""
        result = _extract_geometries_from_mask(FEATURE)
        assert len(result) == 1
        assert result[0]["type"] == "Polygon"

    def test_feature_collection(self):
        """Extract geometries from a FeatureCollection."""
        result = _extract_geometries_from_mask(FEATURE_COLLECTION)
        assert len(result) == 1

    def test_feature_collection_multiple(self):
        """Extract multiple geometries from a FeatureCollection."""
        fc = {
            "type": "FeatureCollection",
            "features": [
                {"type": "Feature", "geometry": POLYGON, "properties": {}},
                {"type": "Feature", "geometry": MULTIPOLYGON, "properties": {}},
            ],
        }
        result = _extract_geometries_from_mask(fc)
        assert len(result) == 2

    def test_empty_feature_collection(self):
        """Empty FeatureCollection returns empty list."""
        fc = {"type": "FeatureCollection", "features": []}
        result = _extract_geometries_from_mask(fc)
        assert result == []

    def test_feature_with_empty_geometry(self):
        """Feature with empty coordinates returns empty list."""
        feature = {
            "type": "Feature",
            "geometry": {"type": "Polygon", "coordinates": []},
            "properties": {},
        }
        result = _extract_geometries_from_mask(feature)
        assert result == []

    def test_feature_with_none_geometry(self):
        """Feature with None geometry returns empty list."""
        feature = {"type": "Feature", "geometry": None, "properties": {}}
        result = _extract_geometries_from_mask(feature)
        assert result == []

    def test_unsupported_type_raises(self):
        """Unsupported geometry type raises ValueError."""
        with pytest.raises(ValueError, match="Unsupported GeoJSON type"):
            _extract_geometries_from_mask({"type": "Point", "coordinates": [0, 0]})

    def test_invalid_input_raises(self):
        """Dict without 'type' key raises ValueError."""
        with pytest.raises(ValueError, match="Invalid GeoJSON"):
            _extract_geometries_from_mask({"no_type": True})

    def test_non_dict_raises(self):
        """Non-dict input raises ValueError."""
        with pytest.raises(ValueError, match="Invalid GeoJSON"):
            _extract_geometries_from_mask("not a dict")


# ---------------------------------------------------------------------------
# Tests for _rasterize_geometries
# ---------------------------------------------------------------------------


class TestRasterizeGeometries:
    """Tests for _rasterize_geometries helper."""

    def test_basic_rasterization(self):
        """Polygon covering lower-left quadrant produces True in that area."""
        geom = {
            "type": "Polygon",
            "coordinates": [
                [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0], [0.0, 0.0]]
            ],
        }
        result = _rasterize_geometries(
            [geom], width=4, height=4, bounds=(0.0, 0.0, 2.0, 2.0)
        )
        assert result.shape == (4, 4)
        assert result.dtype == bool
        # Lower-left quadrant (rows 2-3, cols 0-1) should be True
        assert result[2, 0]  # inside
        assert not result[0, 3]  # outside (upper-right)

    def test_empty_geometry_list(self):
        """No geometries produces all-False mask."""
        result = _rasterize_geometries(
            [], width=4, height=4, bounds=(0.0, 0.0, 2.0, 2.0)
        )
        assert not result.any()

    def test_full_coverage(self):
        """Polygon covering entire extent produces all-True."""
        geom = {
            "type": "Polygon",
            "coordinates": [
                [[0.0, 0.0], [2.0, 0.0], [2.0, 2.0], [0.0, 2.0], [0.0, 0.0]]
            ],
        }
        result = _rasterize_geometries(
            [geom], width=4, height=4, bounds=(0.0, 0.0, 2.0, 2.0)
        )
        assert result.all()


# ---------------------------------------------------------------------------
# Tests for mask_polygon
# ---------------------------------------------------------------------------


class TestMaskPolygon:
    """Tests for the mask_polygon process."""

    def test_basic_mask_outside(self):
        """Pixels outside the polygon are replaced with no-data."""
        # 1-band 4x4 image, bounds=0..2, all values = 10
        data = np.full((1, 4, 4), 10, dtype=np.float64)
        stack = _make_stack(data, bounds=(0.0, 0.0, 2.0, 2.0))

        # Polygon covers lower-left quadrant (0..1, 0..1)
        poly = {
            "type": "Polygon",
            "coordinates": [
                [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0], [0.0, 0.0]]
            ],
        }

        result = mask_polygon(data=stack, mask=poly)

        assert isinstance(result, RasterStack)
        img = list(result.values())[0]
        arr = img.array

        # Pixels inside polygon should still be 10 (unmasked)
        # Pixels outside polygon should be masked (no-data)
        inside_mask = arr.mask[0]

        # At least some pixels inside the polygon should be unmasked
        assert not inside_mask[2, 0] or not inside_mask[3, 0]  # lower-left area
        # Pixels outside should be masked
        assert inside_mask[0, 3]  # upper-right corner

    def test_mask_inside(self):
        """With inside=True, pixels inside the polygon are replaced."""
        data = np.full((1, 4, 4), 10, dtype=np.float64)
        stack = _make_stack(data, bounds=(0.0, 0.0, 2.0, 2.0))

        # Polygon covers lower-left quadrant
        poly = {
            "type": "Polygon",
            "coordinates": [
                [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0], [0.0, 0.0]]
            ],
        }

        result = mask_polygon(data=stack, mask=poly, inside=True)
        img = list(result.values())[0]
        arr = img.array

        # Pixels inside polygon should be masked
        # Pixels outside polygon should remain valid
        assert not arr.mask[0, 0, 3]  # upper-right: outside, should be valid

    def test_replacement_value(self):
        """Masked pixels get a specific replacement value instead of no-data."""
        data = np.full((1, 4, 4), 10, dtype=np.float64)
        stack = _make_stack(data, bounds=(0.0, 0.0, 2.0, 2.0))

        # Full-coverage polygon — nothing gets replaced when inside=False
        # Use a small polygon so some pixels are outside
        poly = {
            "type": "Polygon",
            "coordinates": [
                [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0], [0.0, 0.0]]
            ],
        }

        result = mask_polygon(data=stack, mask=poly, replacement=-999)
        img = list(result.values())[0]
        arr = img.array

        # Pixels outside polygon should have replacement value
        # Upper-right corner is outside
        assert arr[0, 0, 3] == -999

    def test_nodata_preservation(self):
        """Existing no-data values are not overwritten by the masking."""
        data = np.full((1, 4, 4), 10, dtype=np.float64)
        # Mark upper-left pixel as no-data
        data[0, 0, 0] = -9999
        stack = _make_stack(data, bounds=(0.0, 0.0, 2.0, 2.0), nodata=-9999)

        # Full-coverage polygon (all pixels are inside)
        poly = {
            "type": "Polygon",
            "coordinates": [
                [[0.0, 0.0], [2.0, 0.0], [2.0, 2.0], [0.0, 2.0], [0.0, 0.0]]
            ],
        }

        # Even with inside=True (replace inside pixels), no-data should be preserved
        result = mask_polygon(data=stack, mask=poly, inside=True, replacement=0)
        img = list(result.values())[0]

        # The originally-masked pixel should still be masked
        assert img.array.mask[0, 0, 0]

    def test_feature_input(self):
        """mask_polygon accepts a GeoJSON Feature."""
        data = np.full((1, 4, 4), 10, dtype=np.float64)
        stack = _make_stack(data, bounds=(0.0, 0.0, 2.0, 2.0))

        result = mask_polygon(data=stack, mask=FEATURE)
        assert isinstance(result, RasterStack)
        assert len(result) == 1

    def test_feature_collection_input(self):
        """mask_polygon accepts a GeoJSON FeatureCollection."""
        data = np.full((1, 4, 4), 10, dtype=np.float64)
        stack = _make_stack(data, bounds=(0.0, 0.0, 2.0, 2.0))

        result = mask_polygon(data=stack, mask=FEATURE_COLLECTION)
        assert isinstance(result, RasterStack)

    def test_multipolygon_input(self):
        """mask_polygon accepts a MultiPolygon geometry."""
        data = np.full((1, 4, 4), 10, dtype=np.float64)
        stack = _make_stack(data, bounds=(0.0, 0.0, 2.0, 2.0))

        result = mask_polygon(data=stack, mask=MULTIPOLYGON)
        assert isinstance(result, RasterStack)

    def test_empty_geometries_returns_unchanged(self):
        """Empty FeatureCollection returns data unchanged."""
        data = np.full((1, 4, 4), 10, dtype=np.float64)
        stack = _make_stack(data, bounds=(0.0, 0.0, 2.0, 2.0))

        fc = {"type": "FeatureCollection", "features": []}
        result = mask_polygon(data=stack, mask=fc)

        # Should return the same stack reference (no masking applied)
        assert result is stack

    def test_multiple_timestamps(self):
        """mask_polygon processes all timestamps in the RasterStack."""
        data1 = np.full((1, 4, 4), 10, dtype=np.float64)
        data2 = np.full((1, 4, 4), 20, dtype=np.float64)

        img1 = ImageData(
            np.ma.MaskedArray(data1, mask=False),
            crs=EPSG_4326,
            bounds=(0.0, 0.0, 2.0, 2.0),
            band_names=["b0"],
        )
        img2 = ImageData(
            np.ma.MaskedArray(data2, mask=False),
            crs=EPSG_4326,
            bounds=(0.0, 0.0, 2.0, 2.0),
            band_names=["b0"],
        )

        stack = RasterStack.from_images(
            {datetime(2024, 1, 1): img1, datetime(2024, 1, 2): img2}
        )

        poly = {
            "type": "Polygon",
            "coordinates": [
                [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0], [0.0, 0.0]]
            ],
        }

        result = mask_polygon(data=stack, mask=poly)
        assert len(result) == 2

    def test_multi_band(self):
        """Masking is applied to all bands."""
        data = np.arange(48, dtype=np.float64).reshape(3, 4, 4)
        stack = _make_stack(
            data, bounds=(0.0, 0.0, 2.0, 2.0), band_names=["r", "g", "b"]
        )

        # Small polygon in lower-left
        poly = {
            "type": "Polygon",
            "coordinates": [
                [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0], [0.0, 0.0]]
            ],
        }

        result = mask_polygon(data=stack, mask=poly)
        img = list(result.values())[0]

        # All 3 bands should have the same mask pattern
        assert (
            img.array.mask[0, 0, 3]
            == img.array.mask[1, 0, 3]
            == img.array.mask[2, 0, 3]
        )

    def test_dimensions_preserved(self):
        """Output preserves CRS, bounds, and band_names from input."""
        data = np.full((2, 4, 4), 5, dtype=np.float64)
        stack = _make_stack(
            data,
            bounds=(10.0, 20.0, 12.0, 22.0),
            band_names=["nir", "red"],
        )

        poly = {
            "type": "Polygon",
            "coordinates": [
                [[10.0, 20.0], [12.0, 20.0], [12.0, 22.0], [10.0, 22.0], [10.0, 20.0]]
            ],
        }

        result = mask_polygon(data=stack, mask=poly)
        img = list(result.values())[0]

        assert img.crs == EPSG_4326
        assert img.bounds == (10.0, 20.0, 12.0, 22.0)
        assert img.band_names == ["nir", "red"]

    def test_full_coverage_polygon_no_masking(self):
        """Polygon covering entire extent with inside=False masks nothing."""
        data = np.full((1, 4, 4), 42, dtype=np.float64)
        stack = _make_stack(data, bounds=(0.0, 0.0, 2.0, 2.0))

        # Polygon covers entire bounds
        poly = {
            "type": "Polygon",
            "coordinates": [
                [[0.0, 0.0], [2.0, 0.0], [2.0, 2.0], [0.0, 2.0], [0.0, 0.0]]
            ],
        }

        result = mask_polygon(data=stack, mask=poly, inside=False)
        img = list(result.values())[0]

        # No pixels should be masked
        assert not img.array.mask.any()
        # All values should be 42
        np.testing.assert_array_equal(img.array[0], 42)
