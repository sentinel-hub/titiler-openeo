"""Test titiler.openeo.stacapi."""

import pytest
from openeo_pg_parser_networkx.pg_schema import BoundingBox
from pystac import Item
from rio_tiler.models import ImageData

from titiler.openeo.errors import OutputLimitExceeded
from titiler.openeo.reader import SimpleSTACReader
from titiler.openeo.settings import ProcessingSettings
from titiler.openeo.stacapi import LoadCollection, stacApiBackend


def test_processing_settings():
    """Test ProcessingSettings configuration."""
    # Test default value
    settings = ProcessingSettings()
    assert settings.max_pixels == 100_000_000

    # Test custom value via environment variable
    settings = ProcessingSettings(max_pixels=50_000_000)
    assert settings.max_pixels == 50_000_000


# Mock SimpleSTACReader to return fixed dimensions and transform
class MockReader(SimpleSTACReader):
    def part(self, bbox, **kwargs):
        """Mock part method returning ImageData."""
        import numpy

        return ImageData(
            numpy.zeros(
                (1, kwargs.get("height", 1000), kwargs.get("width", 1000)),
                dtype="uint8",
            ),
            assets=kwargs.get("assets", ["B01"]),
            crs=kwargs.get("dst_crs", "EPSG:4326"),
        )


def test_load_collection_pixel_threshold(monkeypatch):
    """Test pixel threshold in load_collection."""

    # Mock STAC item as dict (as returned by STAC API)
    mock_item_dict = {
        "type": "Feature",
        "id": "test-item",
        "stac_version": "1.0.0",
        "stac_extensions": [
            "https://stac-extensions.github.io/projection/v1.1.0/schema.json"
        ],
        "bbox": [0, 0, 1, 1],
        "geometry": {
            "type": "Polygon",
            "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]],
        },
        "properties": {
            "datetime": "2021-01-01T00:00:00Z",
            "proj:crs": "EPSG:4326",
            "proj:transform": [0.0002, 0.0, 0.0, 0.0, -0.0002, 0.0],
        },
        "assets": {
            "B01": {
                "href": "https://example.com/B01.tif",
                "type": "image/tiff; application=geotiff",
            }
        },
        "links": [],
    }

    # Mock _get_items to return our test item as dict
    def mock_get_items(*args, **kwargs):
        # Return pystac.Item for internal processing but reader gets dict
        return [Item.from_dict(mock_item_dict)]

    monkeypatch.setattr("titiler.openeo.reader.SimpleSTACReader", MockReader)
    monkeypatch.setattr(LoadCollection, "_get_items", mock_get_items)

    # Create test instance with smaller threshold
    settings = ProcessingSettings(max_pixels=10_000_000)  # 10 million pixels
    monkeypatch.setattr("titiler.openeo.stacapi.processing_settings", settings)

    backend = stacApiBackend(url="https://example.com")
    loader = LoadCollection(stac_api=backend)

    # Test with dimensions exceeding threshold
    with pytest.raises(OutputLimitExceeded):
        loader.load_collection(
            id="test",
            spatial_extent=BoundingBox(
                west=0, south=0, east=1, north=1, crs="EPSG:4326"
            ),
            bands=["B01"],
            width=15000,
            height=15000,
        )

    # Test with acceptable dimensions
    result = loader.load_collection(
        id="test",
        spatial_extent=BoundingBox(west=0, south=0, east=1, north=1, crs="EPSG:4326"),
        width=1000,
        height=1000,
    )
    assert isinstance(result, dict)
    assert len(result) > 0  # Should contain at least one date-keyed entry
    # First value should be ImageData
    first_value = next(iter(result.values()))
    assert isinstance(first_value, ImageData)


def test_resolution_based_dimension_calculation(monkeypatch):
    """Test resolution-based dimension calculation."""

    # Mock STAC item
    mock_item = Item.from_dict(
        {
            "type": "Feature",
            "id": "test-item",
            "stac_version": "1.0.0",
            "stac_extensions": [
                "https://stac-extensions.github.io/projection/v1.1.0/schema.json"
            ],
            "bbox": [0, 0, 1, 1],
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]],
            },
            "properties": {
                "datetime": "2021-01-01T00:00:00Z",
                "proj:crs": "EPSG:4326",
                "proj:transform": [0.001, 0.0, 0.0, 0.0, -0.001, 0.0],
            },
            "assets": {
                "B01": {
                    "href": "https://example.com/B01.tif",
                    "type": "image/tiff; application=geotiff",
                }
            },
        }
    )

    # Mock _get_items to return our test item
    def mock_get_items(*args, **kwargs):
        return [mock_item]

    # Create patched versions with specific behavior
    monkeypatch.setattr("titiler.openeo.reader.SimpleSTACReader", MockReader)
    monkeypatch.setattr(LoadCollection, "_get_items", mock_get_items)

    # Override mosaic_reader to capture the width/height values
    captured_params = {}

    def mock_mosaic_reader(items, reader, bbox, **kwargs):
        # Capture the parameters for later inspection
        captured_params.update(kwargs)
        # Return a mock ImageData
        import numpy

        return ImageData(
            numpy.zeros((1, 100, 100), dtype="uint8"),
            assets=kwargs.get("assets", ["B01"]),
            crs=kwargs.get("dst_crs", "EPSG:4326"),
        ), None

    monkeypatch.setattr("titiler.openeo.stacapi.mosaic_reader", mock_mosaic_reader)

    # Setup and run the test
    backend = stacApiBackend(url="https://example.com")
    loader = LoadCollection(stac_api=backend)

    # Test with a 1 degree by 1 degree bbox and 0.001 degrees/pixel resolution
    # Should result in approximately 1000x1000 pixels
    spatial_extent = BoundingBox(west=0, south=0, east=1, north=1, crs="EPSG:4326")

    # Call without explicit width/height to trigger resolution-based calculation
    result = loader.load_collection(
        id="test",
        spatial_extent=spatial_extent,
        width=None,
        height=None,
    )

    # Check that calculated dimensions are close to expected (1000x1000)
    # Allow some flexibility due to rounding
    # With RasterStack, dimensions are stored on the stack itself
    from titiler.openeo.processes.implementations.data_model import RasterStack

    assert isinstance(result, RasterStack)
    width = result.width or 0
    height = result.height or 0
    assert 950 <= width <= 1050, f"Width {width} is not within expected range"
    assert 950 <= height <= 1050, f"Height {height} is not within expected range"


def _stac_item_dict(dt: str) -> dict:
    """Minimal valid STAC item dict for the given datetime string."""
    return {
        "type": "Feature",
        "id": f"item-{dt}",
        "stac_version": "1.0.0",
        "stac_extensions": [
            "https://stac-extensions.github.io/projection/v1.1.0/schema.json"
        ],
        "bbox": [0, 0, 1, 1],
        "geometry": {
            "type": "Polygon",
            "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]],
        },
        "properties": {
            "datetime": dt,
            "proj:crs": "EPSG:4326",
            "proj:transform": [0.0002, 0.0, 0.0, 0.0, -0.0002, 0.0],
        },
        "assets": {
            "B01": {
                "href": "https://example.com/B01.tif",
                "type": "image/tiff; application=geotiff",
            }
        },
        "links": [],
    }


def test_load_collection_requests_items_beyond_limit(monkeypatch):
    """load_collection must request max_items + 1 so overflow is detectable.

    Regression for #300: the STAC search silently capped at the first page
    (100, newest-first), dropping whole months/years from wide temporal extents.
    """
    from titiler.openeo.settings import ProcessingSettings

    settings = ProcessingSettings(max_items=20)
    monkeypatch.setattr("titiler.openeo.stacapi.processing_settings", settings)
    monkeypatch.setattr("titiler.openeo.reader.SimpleSTACReader", MockReader)

    captured = {}

    def mock_get_items(self, *args, max_items=None, **kwargs):
        captured["max_items"] = max_items
        return [Item.from_dict(_stac_item_dict("2021-01-01T00:00:00Z"))]

    monkeypatch.setattr(LoadCollection, "_get_items", mock_get_items)

    backend = stacApiBackend(url="https://example.com")
    loader = LoadCollection(stac_api=backend)

    loader.load_collection(
        id="test",
        spatial_extent=BoundingBox(west=0, south=0, east=1, north=1, crs="EPSG:4326"),
        width=64,
        height=64,
    )
    # +1 lets the item-limit guard detect genuine overflow instead of truncating.
    assert captured["max_items"] == settings.max_items + 1


def test_load_collection_raises_when_items_exceed_limit(monkeypatch):
    """Too many items in the extent fails loudly instead of silently truncating."""
    from titiler.openeo.errors import ItemsLimitExceeded
    from titiler.openeo.settings import ProcessingSettings

    settings = ProcessingSettings(max_items=2)
    monkeypatch.setattr("titiler.openeo.stacapi.processing_settings", settings)
    monkeypatch.setattr("titiler.openeo.reader.SimpleSTACReader", MockReader)

    # Backend reports 3 items (> max_items=2); previously this was hidden by the
    # hardcoded 100-item cap and produced silent partial/empty results.
    items = [
        Item.from_dict(_stac_item_dict(f"2021-0{i + 1}-01T00:00:00Z")) for i in range(3)
    ]
    monkeypatch.setattr(LoadCollection, "_get_items", lambda self, *a, **k: items)

    backend = stacApiBackend(url="https://example.com")
    loader = LoadCollection(stac_api=backend)

    with pytest.raises(ItemsLimitExceeded):
        loader.load_collection(
            id="test",
            spatial_extent=BoundingBox(
                west=0, south=0, east=1, north=1, crs="EPSG:4326"
            ),
            width=64,
            height=64,
        )
