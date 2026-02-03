"""Tests for reader module."""

import datetime

import pytest
import rasterio
from openeo_pg_parser_networkx.pg_schema import BoundingBox
from pystac import Item

from titiler.openeo.errors import OutputLimitExceeded
from titiler.openeo.reader import (
    SimpleSTACReader,
    _calculate_dimensions,
    _check_pixel_limit,
    _estimate_output_dimensions,
    _get_assets_resolutions,
    _reproject_resolution,
)


@pytest.fixture
def complex_stac_items():
    """Create a set of complex STAC items with different CRS and resolutions."""
    items: list[Item] = []

    # Item 1: Sentinel-2 like with UTM bands
    items.append(
        Item.from_dict(
            {
                "type": "Feature",
                "stac_version": "1.0.0",
                "stac_extensions": [
                    "https://stac-extensions.github.io/projection/v1.1.0/schema.json",
                    "https://stac-extensions.github.io/eo/v1.0.0/schema.json",
                ],
                "id": "sentinel-item",
                "bbox": [0, 40, 6, 46],
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[0, 40], [6, 40], [6, 46], [0, 46], [0, 40]]],
                },
                "properties": {
                    "datetime": "2025-01-01T00:00:00Z",
                    "proj:epsg": 32631,
                    "proj:shape": [10980, 10980],
                    "proj:bbox": [
                        243900.35202972306,
                        4427757.218738374,
                        756099.6479702785,
                        5098424.079643565,
                    ],
                },
                "assets": {
                    "B02": {
                        "href": "https://example.com/B02.tif",
                        "proj:epsg": 32631,
                        "proj:shape": [10980, 10980],
                        "proj:transform": [10, 0, 0, 0, -10, 0, 0, 0, 1],
                    },
                    "B08": {
                        "href": "https://example.com/B08.tif",
                        "proj:epsg": 32631,
                        "proj:shape": [10980, 10980],
                        "proj:transform": [10, 0, 0, 0, -10, 0, 0, 0, 1],
                    },
                },
            }
        )
    )

    # Item 2: Mixed resolution bands in different CRS
    items.append(
        Item.from_dict(
            {
                "type": "Feature",
                "stac_version": "1.0.0",
                "stac_extensions": [
                    "https://stac-extensions.github.io/projection/v1.1.0/schema.json",
                ],
                "id": "mixed-item",
                "bbox": [0, 0, 10, 10],
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]]],
                },
                "properties": {
                    "datetime": "2025-01-01T00:00:00Z",
                },
                "assets": {
                    "lowres": {
                        "href": "https://example.com/lowres.tif",
                        "proj:epsg": 4326,
                        "proj:shape": [100, 100],
                        "proj:transform": [0.1, 0, 0, 0, -0.1, 10, 0, 0, 1],
                    },
                    "highres": {
                        "href": "https://example.com/highres.tif",
                        "proj:epsg": 3857,
                        "proj:shape": [1000, 1000],
                        "proj:transform": [10, 0, 0, 0, -10, 0, 0, 0, 1],
                    },
                },
            }
        )
    )

    return items


@pytest.fixture
def sample_stac_item():
    """Create a sample STAC item."""
    return Item.from_dict(
        {
            "type": "Feature",
            "stac_version": "1.0.0",
            "stac_extensions": [
                "https://stac-extensions.github.io/projection/v1.1.0/schema.json",
                "https://stac-extensions.github.io/eo/v1.0.0/schema.json",
            ],
            "id": "test-item",
            "bbox": [0, 0, 10, 10],
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]]],
            },
            "properties": {
                "datetime": "2025-01-01T00:00:00Z",
                "proj:epsg": 4326,
                "proj:shape": [100, 100],
                "proj:transform": [0.1, 0, 0, 0, -0.1, 10, 0, 0, 1],
            },
            "assets": {
                "B01": {
                    "href": "https://example.com/B01.tif",
                    "type": "image/tiff; application=geotiff",
                    "proj:epsg": 32631,
                    "proj:shape": [1000, 1000],
                    "proj:transform": [10, 0, 0, 0, -10, 0, 0, 0, 1],
                },
                "B02": {
                    "href": "https://example.com/B02.tif",
                    "type": "image/tiff; application=geotiff",
                    "proj:shape": [2000, 2000],
                },
            },
        }
    )


def test_get_assets_resolutions(sample_stac_item):
    """Test resolution extraction from STAC item."""
    # Test with all bands
    with SimpleSTACReader(sample_stac_item) as src_dst:
        resolutions = _get_assets_resolutions(sample_stac_item, src_dst)

        # Check B01 (with explicit CRS and transform)
        assert "B01" in resolutions
        x_res, y_res, crs = resolutions["B01"]
        assert x_res == 10.0
        assert y_res == 10.0
        assert crs.to_epsg() == 32631

        # Check B02 (with only transform)
        assert "B02" in resolutions
        x_res, y_res, crs = resolutions["B02"]
        assert x_res == 0.1
        assert y_res == 0.1
        assert crs.to_epsg() == 4326  # Using default item CRS

    # Test with specific bands
    with SimpleSTACReader(sample_stac_item) as src_dst:
        resolutions = _get_assets_resolutions(sample_stac_item, src_dst, bands=["B01"])
        assert len(resolutions) == 1
        assert "B01" in resolutions
        assert "B02" not in resolutions

    # Test with missing bands
    with SimpleSTACReader(sample_stac_item) as src_dst:
        resolutions = _get_assets_resolutions(sample_stac_item, src_dst, bands=["B03"])
        assert len(resolutions) == 0

    # Test with item without any projection info
    item_without_metadata = Item.from_dict(
        {
            "type": "Feature",
            "stac_version": "1.0.0",
            "id": "test-item",
            "bbox": [0, 0, 10, 10],
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]]],
            },
            "properties": {"datetime": "2025-01-01T00:00:00Z"},
            "assets": {
                "B01": {
                    "href": "https://example.com/B01.tif",
                }
            },
        }
    )
    with SimpleSTACReader(item_without_metadata) as src_dst:
        resolutions = _get_assets_resolutions(item_without_metadata, src_dst)
        assert "B01" not in resolutions


def test_reproject_resolution():
    """Test resolution reprojection."""
    src_crs = rasterio.crs.CRS.from_epsg(4326)
    dst_crs = rasterio.crs.CRS.from_epsg(3857)
    bbox = [0, 0, 1, 1]
    x_res, y_res = _reproject_resolution(src_crs, dst_crs, bbox, 0.1, 0.1)
    assert x_res is not None
    assert y_res is not None
    assert x_res != 0.1  # Should be different after reprojection
    assert y_res != 0.1  # Should be different after reprojection


def test_calculate_dimensions():
    """Test dimension calculation."""
    bbox = [0, 0, 10, 10]

    # Test with native resolution - both dimensions provided
    width, height = _calculate_dimensions(bbox, 0.1, 0.1, width=100, height=200)
    assert width == 100
    assert height == 200

    # Test with native resolution - only width provided
    native_width = 100
    native_height = 200
    x_resolution = (bbox[2] - bbox[0]) / native_width
    y_resolution = (bbox[3] - bbox[1]) / native_height
    aspect_ratio = native_width / native_height

    width, height = _calculate_dimensions(
        bbox, x_resolution, y_resolution, width=50, height=None
    )
    assert width == 50
    assert height == int(round(50 / aspect_ratio))

    # Test with native resolution - only height provided
    width, height = _calculate_dimensions(
        bbox, x_resolution, y_resolution, width=None, height=100
    )
    assert height == 100
    assert width == int(round(100 * aspect_ratio))

    # Test with resolution - no dimensions provided
    width, height = _calculate_dimensions(bbox, x_resolution, y_resolution)
    assert width == native_width
    assert height == native_height

    # Test with no resolution - both dimensions provided
    width, height = _calculate_dimensions(bbox, None, None, width=100, height=200)
    assert width == 100
    assert height == 200

    # Test with no resolution - only width provided
    width, height = _calculate_dimensions(bbox, None, None, width=100, height=None)
    assert width == 100
    assert height == 1024

    # Test with no resolution - only height provided
    width, height = _calculate_dimensions(bbox, None, None, width=None, height=100)
    assert width == 1024
    assert height == 100

    # Test with no resolution - no dimensions provided
    width, height = _calculate_dimensions(bbox, None, None)
    assert width == 1024
    assert height == 1024


def test_check_pixel_limit():
    """Test pixel count limit check."""
    # Test within limit
    _check_pixel_limit(100, 100, 2, 2)

    # Test exceeding limit with multiple items
    with pytest.raises(OutputLimitExceeded) as exc_info:
        _check_pixel_limit(10000, 10000, 2, 3)  # 600 million pixels
    error_msg = str(exc_info.value)
    assert "10000x10000 pixels x 2 items x 3 bands" in error_msg
    assert "600,000,000 total pixels" in error_msg  # Test thousands separator
    assert "max allowed: 100,000,000 pixels" in error_msg

    # Test exceeding limit with single item
    with pytest.raises(OutputLimitExceeded) as exc_info:
        _check_pixel_limit(15000, 15000, 1, 1)  # 225 million pixels
    error_msg = str(exc_info.value)
    assert "15000x15000 pixels" in error_msg
    assert "225,000,000 total pixels" in error_msg
    assert "max allowed: 100,000,000 pixels" in error_msg

    # Test with None dimensions (should convert to 0)
    width_int, height_int = None, None
    _check_pixel_limit(
        width_int, height_int, 2, 1
    )  # Should not raise error for 0 pixels


def test_multiple_items_resolution(complex_stac_items):
    """Test resolution handling with multiple items."""
    # Create extent in web mercator
    extent = BoundingBox(
        crs="EPSG:3857",
        west=0,
        south=0,
        east=10000,
        north=10000,
    )

    # Test combining bands from different UTM zones
    item1 = complex_stac_items[0].full_copy()  # Sentinel in UTM 31N
    item2 = complex_stac_items[0].full_copy()  # Create a copy
    # Modify to UTM 32N
    item2.extra_fields["proj:epsg"] = 32632
    for asset in item2.assets.values():
        asset.extra_fields["proj:epsg"] = 32632

    # Explicitly request web mercator output (matches the old behavior)
    result = _estimate_output_dimensions(
        [item1, item2],
        extent,
        ["B02"],  # Same band from different UTM zones
        width=None,
        height=None,
        target_crs=3857,  # Explicit target CRS
    )

    # Since both items have same resolution, output should maintain it
    assert result["crs"].to_epsg() == 3857  # Target CRS
    # Expected size based on 10m resolution in UTM (roughly)
    assert 900 <= result["width"] <= 1100
    assert 900 <= result["height"] <= 1100


def test_estimate_output_dimensions(sample_stac_item, complex_stac_items):
    """Test complete output dimension estimation."""
    # Test with full extent using UTM asset
    full_extent = BoundingBox(west=0, south=0, east=5, north=5, crs="EPSG:4326")
    with pytest.raises(OutputLimitExceeded) as exc_info:
        _estimate_output_dimensions(
            [sample_stac_item],
            full_extent,
            ["B01"],
        )

    # Test with cropped extent
    cropped_extent = BoundingBox(west=4.9, south=4.9, east=5, north=5, crs="EPSG:4326")
    result_cropped = _estimate_output_dimensions(
        [sample_stac_item],
        cropped_extent,
        ["B01"],
    )

    assert "width" in result_cropped
    assert "height" in result_cropped
    assert "crs" in result_cropped
    assert "bbox" in result_cropped
    assert result_cropped["crs"].to_epsg() == 4326  # Target CRS is from spatial_extent
    # Dimensions should be based on the B01 asset's transform after reprojection
    assert result_cropped["width"] > 0
    assert result_cropped["height"] > 0

    # Test with specified width
    result = _estimate_output_dimensions(
        [sample_stac_item],
        full_extent,
        ["B01"],
        width=1024,
    )
    assert result["width"] == 1024
    assert result["height"] > 0  # Should be proportional

    # Test with specified height
    result = _estimate_output_dimensions(
        [sample_stac_item],
        full_extent,
        ["B01"],
        height=1024,
    )
    assert result["height"] == 1024
    assert result["width"] > 0  # Should be proportional

    # Test output size limit with multiple items
    with pytest.raises(OutputLimitExceeded) as exc_info:
        item2 = sample_stac_item.full_copy()  # Create a copy for testing
        item2.datetime = datetime.datetime(2025, 1, 2)  # Different datetime
        _estimate_output_dimensions(
            [sample_stac_item, item2],
            full_extent,
            ["B01"],
            width=10000,
            height=10000,
        )
    error_msg = str(exc_info.value)
    assert "10000x10000 pixels x 2 items x 1 bands" in error_msg
    assert "200,000,000 total pixels" in error_msg
    assert "max allowed: 100,000,000 pixels" in error_msg

    # Test mixed resolution and CRS combination
    mixed_extent = BoundingBox(
        crs="EPSG:4326",
        west=0,
        south=0,
        east=1,
        north=1,
    )

    # Test combining sentinel bands
    result = _estimate_output_dimensions(
        [complex_stac_items[0]],  # Sentinel-like item
        None,
        ["B02", "B08"],
        width=None,
        height=None,
        check_max_pixels=False,  # Disable limit for this test
    )
    # No spatial extent, uses native CRS from item (UTM 32631)
    assert result["crs"].to_epsg() == 32631

    # Test combining different resolution bands
    result = _estimate_output_dimensions(
        [complex_stac_items[1]],  # Mixed resolutions item
        mixed_extent,
        ["lowres", "highres"],
        width=None,
        height=None,
        check_max_pixels=False,  # Disable limit for this test
    )
    # Should use highest resolution (smallest pixel size)
    assert result["width"] > 100  # Should be higher than lowres dimensions
    assert result["height"] > 100

    # Test combining bands from different items with explicit target_crs
    result = _estimate_output_dimensions(
        complex_stac_items,  # Both items
        mixed_extent,
        ["B02", "lowres"],  # Mixing bands from different items
        width=None,
        height=None,
        check_max_pixels=False,  # Disable limit for this test
        target_crs=4326,  # Explicitly request WGS84 output
    )
    # Should use explicitly specified target CRS
    assert result["crs"].to_epsg() == 4326
    assert result["width"] > 0
    assert result["height"] > 0


def test_target_crs_parameter(complex_stac_items):
    """Test target_crs parameter behavior in _estimate_output_dimensions."""
    # Create extent in WGS84
    extent = BoundingBox(
        crs="EPSG:4326",
        west=0,
        south=40,
        east=6,
        north=46,
    )

    # Create an item with full projection info at item level
    item_with_proj = Item.from_dict(
        {
            "type": "Feature",
            "stac_version": "1.0.0",
            "stac_extensions": [
                "https://stac-extensions.github.io/projection/v1.1.0/schema.json",
            ],
            "id": "utm-item",
            "bbox": [0, 40, 6, 46],
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[0, 40], [6, 40], [6, 46], [0, 46], [0, 40]]],
            },
            "properties": {
                "datetime": "2025-01-01T00:00:00Z",
                "proj:epsg": 32631,
                "proj:shape": [10980, 10980],
                "proj:transform": [10, 0, 243900, 0, -10, 5098424, 0, 0, 1],
            },
            "assets": {
                "B02": {
                    "href": "https://example.com/B02.tif",
                    "proj:epsg": 32631,
                    "proj:shape": [10980, 10980],
                    "proj:transform": [10, 0, 243900, 0, -10, 5098424, 0, 0, 1],
                },
            },
        }
    )

    # Test 1: target_crs=None should use native CRS from first item (UTM 32631)
    result = _estimate_output_dimensions(
        [item_with_proj],
        extent,
        ["B02"],
        width=1024,
        height=1024,
        target_crs=None,  # Should use native CRS
    )
    # Native CRS of first item is UTM 32631
    assert result["crs"].to_epsg() == 32631
    # bounds_crs should be the bbox CRS (4326)
    assert result["bounds_crs"].to_epsg() == 4326

    # Test 2: Explicit target_crs should override native CRS
    result = _estimate_output_dimensions(
        [item_with_proj],
        extent,
        ["B02"],
        width=1024,
        height=1024,
        target_crs=3857,  # Request Web Mercator
    )
    assert result["crs"].to_epsg() == 3857
    assert result["bounds_crs"].to_epsg() == 4326

    # Test 3: target_crs as WKT string
    result = _estimate_output_dimensions(
        [item_with_proj],
        extent,
        ["B02"],
        width=1024,
        height=1024,
        target_crs="EPSG:32632",  # UTM zone 32N as string
    )
    assert result["crs"].to_epsg() == 32632

    # Test 4: No spatial_extent, target_crs=None should use native CRS
    result = _estimate_output_dimensions(
        [item_with_proj],
        None,  # No spatial extent
        ["B02"],
        width=1024,
        height=1024,
        target_crs=None,
    )
    # Should use native CRS from items
    assert result["crs"].to_epsg() == 32631


def test_target_crs_with_resolution(complex_stac_items):
    """Test that target_crs properly affects resolution calculations."""
    # Create a small extent in UTM meters (roughly 10km x 10km)
    extent = BoundingBox(
        crs="EPSG:32631",
        west=500000,
        south=4500000,
        east=510000,
        north=4510000,
    )

    # Test with native CRS (UTM) - resolution should be reasonable
    result = _estimate_output_dimensions(
        [complex_stac_items[0]],
        extent,
        ["B02"],
        width=None,  # Let it calculate from resolution
        height=None,
        check_max_pixels=False,
        target_crs=32631,  # Keep in UTM
    )
    # 10km / 10m resolution = ~1000 pixels
    assert 500 <= result["width"] <= 2000
    assert 500 <= result["height"] <= 2000
    assert result["crs"].to_epsg() == 32631


def test_target_crs_from_item_properties():
    """Test that native CRS is detected from item properties when proj:transform/shape are missing.

    This covers the Earth Search case where items have proj:epsg but not full projection info.
    """
    extent = BoundingBox(
        crs="EPSG:4326",
        west=0,
        south=40,
        east=6,
        north=46,
    )

    # Item with only proj:epsg at item level (no transform/shape)
    # This is how Earth Search Sentinel-2 items typically look
    item_with_epsg_only = Item.from_dict(
        {
            "type": "Feature",
            "stac_version": "1.0.0",
            "stac_extensions": [
                "https://stac-extensions.github.io/projection/v1.1.0/schema.json",
            ],
            "id": "earthsearch-style-item",
            "bbox": [0, 40, 6, 46],
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[0, 40], [6, 40], [6, 46], [0, 46], [0, 40]]],
            },
            "properties": {
                "datetime": "2025-01-01T00:00:00Z",
                "proj:epsg": 32631,  # Only EPSG, no shape/transform
            },
            "assets": {
                "B02": {
                    "href": "https://example.com/B02.tif",
                    "proj:epsg": 32631,
                    "proj:shape": [10980, 10980],
                    "proj:transform": [10, 0, 243900, 0, -10, 5098424, 0, 0, 1],
                },
            },
        }
    )

    # Test: target_crs=None should detect native CRS from item properties
    result = _estimate_output_dimensions(
        [item_with_epsg_only],
        extent,
        ["B02"],
        width=1024,
        height=1024,
        target_crs=None,
    )
    # Should detect UTM 32631 from item properties
    assert result["crs"].to_epsg() == 32631
    assert result["bounds_crs"].to_epsg() == 4326


def test_target_crs_from_asset_properties():
    """Test that native CRS is detected from asset properties as fallback."""
    extent = BoundingBox(
        crs="EPSG:4326",
        west=0,
        south=40,
        east=6,
        north=46,
    )

    # Item with proj:epsg only at asset level (not in item properties)
    item_with_asset_epsg = Item.from_dict(
        {
            "type": "Feature",
            "stac_version": "1.0.0",
            "stac_extensions": [
                "https://stac-extensions.github.io/projection/v1.1.0/schema.json",
            ],
            "id": "asset-crs-item",
            "bbox": [0, 40, 6, 46],
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[0, 40], [6, 40], [6, 46], [0, 46], [0, 40]]],
            },
            "properties": {
                "datetime": "2025-01-01T00:00:00Z",
                # No proj:epsg at item level
            },
            "assets": {
                "B02": {
                    "href": "https://example.com/B02.tif",
                    "proj:epsg": 32632,  # CRS only at asset level
                    "proj:shape": [10980, 10980],
                    "proj:transform": [10, 0, 243900, 0, -10, 5098424, 0, 0, 1],
                },
            },
        }
    )

    # Test: target_crs=None should detect native CRS from asset properties
    result = _estimate_output_dimensions(
        [item_with_asset_epsg],
        extent,
        ["B02"],
        width=1024,
        height=1024,
        target_crs=None,
    )
    # Should detect UTM 32632 from asset properties
    assert result["crs"].to_epsg() == 32632
    assert result["bounds_crs"].to_epsg() == 4326
