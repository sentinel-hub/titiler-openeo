"""Tests for spatial extent type compatibility.

This test ensures that BoundingBox from openEO pg_schema is properly
accepted as a spatial extent parameter, preventing type validation errors.
"""

import pytest
from openeo_pg_parser_networkx.pg_schema import BoundingBox

from titiler.openeo.models.openapi import SpatialExtent


def test_spatial_extent_is_boundingbox():
    """Test that SpatialExtent is aliased to BoundingBox."""
    assert (
        SpatialExtent is BoundingBox
    ), "SpatialExtent should be aliased to BoundingBox from openEO pg_schema"


def test_boundingbox_creation():
    """Test that BoundingBox can be created with expected parameters."""
    bbox = BoundingBox(
        west=-180.0, south=-90.0, east=180.0, north=90.0, crs="EPSG:4326"
    )

    assert bbox.west == -180.0
    assert bbox.south == -90.0
    assert bbox.east == 180.0
    assert bbox.north == 90.0
    assert bbox.crs is not None


def test_boundingbox_with_optional_params():
    """Test that BoundingBox supports optional base and height parameters."""
    bbox = BoundingBox(
        west=0.0,
        south=0.0,
        east=10.0,
        north=10.0,
        base=0.0,
        height=100.0,
        crs="EPSG:4326",
    )

    assert bbox.base == 0.0
    assert bbox.height == 100.0


def test_boundingbox_type_validation():
    """Test that BoundingBox instances pass type validation."""
    from typing import Optional

    from pydantic import BaseModel, ValidationError

    class TestModel(BaseModel):
        spatial_extent: Optional[BoundingBox] = None

    # Test with valid BoundingBox
    bbox = BoundingBox(west=0.0, south=0.0, east=10.0, north=10.0, crs="EPSG:4326")

    model = TestModel(spatial_extent=bbox)
    assert model.spatial_extent == bbox

    # Test with None
    model_none = TestModel(spatial_extent=None)
    assert model_none.spatial_extent is None

    # Test that invalid types are rejected
    with pytest.raises(ValidationError):
        TestModel(spatial_extent="invalid")


def test_spatial_extent_compatibility():
    """Test that SpatialExtent (aliased to BoundingBox) is compatible with expected usage."""
    # Create using the BoundingBox alias
    extent = SpatialExtent(
        west=-10.0, south=-10.0, east=10.0, north=10.0, crs="EPSG:4326"
    )

    # Verify it's actually a BoundingBox instance
    assert isinstance(extent, BoundingBox)

    # Verify it has all expected properties
    assert extent.west == -10.0
    assert extent.south == -10.0
    assert extent.east == 10.0
    assert extent.north == 10.0
    assert extent.crs is not None


def test_boundingbox_crs_validation():
    """Test that BoundingBox properly validates and parses CRS values."""
    # Test with EPSG code as integer
    bbox_int = BoundingBox(west=0.0, south=0.0, east=10.0, north=10.0, crs=4326)
    assert bbox_int.crs is not None

    # Test with EPSG code as string
    bbox_str = BoundingBox(west=0.0, south=0.0, east=10.0, north=10.0, crs="EPSG:4326")
    assert bbox_str.crs is not None

    # Test with None (should use default)
    bbox_none = BoundingBox(west=0.0, south=0.0, east=10.0, north=10.0, crs=None)
    assert bbox_none.crs is not None  # Should have default CRS
