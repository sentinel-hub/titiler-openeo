"""Tests for reader module."""

import pytest
import rasterio

from titiler.openeo.errors import (
    MixedCRSError,
    OutputLimitExceeded,
    ProcessParameterMissing,
)
from titiler.openeo.models import SpatialExtent
from titiler.openeo.reader import (
    SimpleSTACReader,
    _calculate_dimensions,
    _check_pixel_limit,
    _estimate_output_dimensions,
    _get_item_resolutions,
    _reproject_resolution,
    _validate_input_parameters,
)


@pytest.fixture
def sample_stac_item():
    """Create a sample STAC item."""
    return {
        "id": "test-item",
        "bbox": [0, 0, 10, 10],
        "assets": {
            "B01": {
                "href": "https://example.com/B01.tif",
                "proj:transform": [10, 0, 0, 0, -10, 0],
            }
        },
    }


@pytest.fixture
def sample_spatial_extent():
    """Create a sample spatial extent."""
    return SpatialExtent(west=0, south=0, east=10, north=10, crs="EPSG:4326")


def test_validate_input_parameters(sample_spatial_extent, sample_stac_item):
    """Test input parameter validation."""
    # Test valid inputs
    _validate_input_parameters(sample_spatial_extent, [sample_stac_item], ["B01"])

    # Test missing spatial extent
    with pytest.raises(ProcessParameterMissing, match="spatial_extent"):
        _validate_input_parameters(None, [sample_stac_item], ["B01"])

    # Test empty items list
    with pytest.raises(ProcessParameterMissing, match="items"):
        _validate_input_parameters(sample_spatial_extent, [], ["B01"])

    # Test missing bands
    with pytest.raises(ProcessParameterMissing, match="bands"):
        _validate_input_parameters(sample_spatial_extent, [sample_stac_item], None)


def test_get_item_resolutions(sample_stac_item, sample_spatial_extent):
    """Test resolution extraction from STAC item."""
    # Test with proj:transform
    with SimpleSTACReader(sample_stac_item) as src_dst:
        x_res, y_res = _get_item_resolutions(
            sample_stac_item, src_dst, sample_spatial_extent
        )
        assert len(x_res) > 0
        assert len(y_res) > 0
        assert x_res[0] == 10  # From proj:transform
        assert y_res[0] == 10  # From proj:transform

    # Test with proj:shape
    item_with_shape = {
        "id": "test-item",
        "bbox": [0, 0, 10, 10],
        "assets": {
            "B01": {
                "href": "https://example.com/B01.tif",
                "proj:shape": [100, 100],
            }
        },
    }
    with SimpleSTACReader(item_with_shape) as src_dst:
        x_res, y_res = _get_item_resolutions(
            item_with_shape, src_dst, sample_spatial_extent
        )
        assert len(x_res) > 0
        assert len(y_res) > 0
        assert x_res[0] == 0.1  # 10/100
        assert y_res[0] == 0.1  # 10/100

    # Test fallback to default resolution
    item_without_metadata = {
        "id": "test-item",
        "bbox": [0, 0, 10, 10],
        "assets": {
            "B01": {
                "href": "https://example.com/B01.tif",
            }
        },
    }
    with SimpleSTACReader(item_without_metadata) as src_dst:
        x_res, y_res = _get_item_resolutions(
            item_without_metadata, src_dst, sample_spatial_extent
        )
        assert len(x_res) > 0
        assert len(y_res) > 0
        assert x_res[0] == 1024  # Default resolution
        assert y_res[0] == 1024  # Default resolution


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

    # Test with specified dimensions
    width, height = _calculate_dimensions(bbox, None, None, width=100, height=200)
    assert width == 100
    assert height == 200

    # Test with resolution
    width, height = _calculate_dimensions(bbox, x_resolution=0.1, y_resolution=0.1)
    assert width == 100  # 10 / 0.1
    assert height == 100  # 10 / 0.1

    # Test default fallback
    width, height = _calculate_dimensions(bbox, None, None)
    assert width == 1024
    assert height == 1024


def test_check_pixel_limit():
    """Test pixel count limit check."""
    # Test within limit
    _check_pixel_limit(100, 100, [{"id": "item1"}, {"id": "item2"}])

    # Test exceeding limit with multiple items
    with pytest.raises(OutputLimitExceeded) as exc_info:
        _check_pixel_limit(10000, 10000, [{"id": "item1"}, {"id": "item2"}])
    error_msg = str(exc_info.value)
    assert "10000x10000 pixels x 2 items" in error_msg
    assert "200,000,000 total pixels" in error_msg  # Test thousands separator
    assert "max allowed: 100,000,000 pixels" in error_msg

    # Test exceeding limit with single item
    with pytest.raises(OutputLimitExceeded) as exc_info:
        _check_pixel_limit(15000, 15000, [{"id": "item1"}])  # 225 million pixels
    error_msg = str(exc_info.value)
    assert "15000x15000 pixels" in error_msg
    assert "225,000,000 total pixels" in error_msg
    assert "max allowed: 100,000,000 pixels" in error_msg

    # Test with None dimensions (should convert to 0)
    width_int, height_int = None, None
    items = [{"id": "item1"}, {"id": "item2"}]
    _check_pixel_limit(
        width_int, height_int, items
    )  # Should not raise error for 0 pixels


def test_estimate_output_dimensions(sample_stac_item, sample_spatial_extent):
    """Test complete output dimension estimation."""
    # Test successful estimation
    result = _estimate_output_dimensions(
        [sample_stac_item],
        sample_spatial_extent,
        ["B01"],
    )
    assert "width" in result
    assert "height" in result
    assert "crs" in result
    assert "bbox" in result

    # Test mixed CRS error
    item2 = {
        "id": "test-item-2",
        "bbox": [0, 0, 10, 10],
        "proj": {
            "epsg": 3857,
            "transform": [10, 0, 0, 0, -10, 0, 0, 0, 1],
            "shape": [100, 100],
        },
        "assets": {
            "B01": {
                "href": "https://example.com/B01.tif",
                "proj:transform": [10, 0, 0, 0, -10, 0, 0, 0, 1],
            }
        },
    }
    with pytest.raises(MixedCRSError) as exc_info:
        _estimate_output_dimensions(
            [sample_stac_item, item2],
            sample_spatial_extent,
            ["B01"],
        )
    assert "Mixed CRS in items" in str(exc_info.value)
    assert "found EPSG:3857" in str(exc_info.value)
    assert "expected EPSG:4326" in str(exc_info.value)

    # Test output size limit with single item
    with pytest.raises(OutputLimitExceeded) as exc_info:
        _estimate_output_dimensions(
            [sample_stac_item],
            sample_spatial_extent,
            ["B01"],
            width=15000,
            height=15000,
        )
    error_msg = str(exc_info.value)
    assert "15000x15000 pixels" in error_msg
    assert "225,000,000 total pixels" in error_msg
    assert "max allowed: 100,000,000 pixels" in error_msg

    # Test output size limit with multiple items
    with pytest.raises(OutputLimitExceeded) as exc_info:
        _estimate_output_dimensions(
            [sample_stac_item, sample_stac_item.copy()],
            sample_spatial_extent,
            ["B01"],
            width=10000,
            height=10000,
        )
    error_msg = str(exc_info.value)
    assert "10000x10000 pixels x 2 items" in error_msg
    assert "200,000,000 total pixels" in error_msg
    assert "max allowed: 100,000,000 pixels" in error_msg

    # Test with default dimensions
    result = _estimate_output_dimensions(
        [sample_stac_item],
        sample_spatial_extent,
        ["B01"],
        width=None,
        height=None,
    )
    assert isinstance(result["width"], int)
    assert isinstance(result["height"], int)
    assert result["width"] > 0
    assert result["height"] > 0
