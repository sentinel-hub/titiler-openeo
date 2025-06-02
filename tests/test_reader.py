"""Tests for reader module."""

from typing import Dict, Optional, Any
import attr
import pytest
import rasterio
from openeo_pg_parser_networkx.pg_schema import BoundingBox
from rasterio.transform import from_bounds, Affine

from titiler.openeo.errors import (
    MixedCRSError,
    OutputLimitExceeded,
    ProcessParameterMissing,
)
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
                "proj:transform": [1, 0, 0, 0, -1, 0, 0, 0, 1],
            }
        },
    }

@attr.s(auto_attribs=True)
class MockSimpleSTACReader:
    """Mock SimpleSTACReader."""
    input: Dict[str, Any]
    _bounds: Optional[list] = attr.ib(init=False)
    _transform: Optional[Affine] = attr.ib(init=False)
    _crs: Optional[rasterio.crs.CRS] = attr.ib(init=False)

    def __attrs_post_init__(self) -> None:
        """Initialize reader attributes."""
        self._bounds = self.input["bbox"]
        self._crs = rasterio.crs.CRS.from_epsg(4326)
        self._transform = None

        # Initialize transform from asset if available
        if asset := self.input["assets"].get("B01"):
            if "proj:transform" in asset:
                t = asset["proj:transform"]
                self._transform = Affine(t[0], t[1], t[2], t[3], t[4], t[5])
            elif "proj:shape" in asset:
                # Create transform from bounds and shape
                west, south, east, north = self._bounds
                width, height = asset["proj:shape"]
                self._transform = from_bounds(west, south, east, north, width, height)

    @property
    def bounds(self):
        """Return item bounds."""
        return self._bounds

    @property
    def transform(self):
        """Return transform."""
        return self._transform

    @property
    def crs(self):
        """Return CRS."""
        return self._crs

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        pass

@pytest.fixture
def sample_spatial_extent():
    """Create a sample spatial extent."""
    return BoundingBox(west=0, south=0, east=10, north=10, crs="EPSG:4326")

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
    with MockSimpleSTACReader(sample_stac_item) as src_dst:
        x_res, y_res = _get_item_resolutions(
            sample_stac_item, src_dst, sample_spatial_extent
        )
        assert len(x_res) > 0
        assert len(y_res) > 0
        assert x_res[0] == 1.0  # From proj:transform
        assert y_res[0] == 1.0  # From proj:transform

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
    with MockSimpleSTACReader(item_with_shape) as src_dst:
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
    with MockSimpleSTACReader(item_without_metadata) as src_dst:
        x_res, y_res = _get_item_resolutions(
            item_without_metadata, src_dst, sample_spatial_extent
        )
        assert len(x_res) > 0
        assert len(y_res) > 0
        assert x_res[0] == 1.0  # Default resolution
        assert y_res[0] == 1.0  # Default resolution

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

    width, height = _calculate_dimensions(bbox, x_resolution, y_resolution, width=50, height=None)
    assert width == 50
    assert height == int(round(50 / aspect_ratio))

    # Test with native resolution - only height provided
    width, height = _calculate_dimensions(bbox, x_resolution, y_resolution, width=None, height=100)
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

def test_estimate_output_dimensions(sample_stac_item, sample_spatial_extent, monkeypatch):
    """Test complete output dimension estimation."""
    monkeypatch.setattr("titiler.openeo.reader.SimpleSTACReader", MockSimpleSTACReader)

    # Test with full extent
    result = _estimate_output_dimensions(
        [sample_stac_item],
        sample_spatial_extent,
        ["B01"],
    )
    assert "width" in result
    assert "height" in result
    assert "crs" in result
    assert "bbox" in result
    assert result["width"] == 10
    assert result["height"] == 10

    # Test with cropped extent
    cropped_extent = BoundingBox(west=0, south=0, east=5, north=5, crs="EPSG:4326")
    result_cropped = _estimate_output_dimensions(
        [sample_stac_item],
        cropped_extent,
        ["B01"],
    )
    # Dimensions should be half when extent is halved
    assert result_cropped["width"] == result["width"] // 2
    assert result_cropped["height"] == result["height"] // 2

    # Test with specified width
    result = _estimate_output_dimensions(
        [sample_stac_item],
        sample_spatial_extent,
        ["B01"],
        width=100,
    )
    assert result["width"] == 100
    assert result["height"] > 0  # Should be proportional

    # Test with specified height
    result = _estimate_output_dimensions(
        [sample_stac_item],
        sample_spatial_extent,
        ["B01"],
        height=100,
    )
    assert result["height"] == 100
    assert result["width"] > 0  # Should be proportional

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
