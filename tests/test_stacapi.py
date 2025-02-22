"""Test titiler.openeo.stacapi."""

import pytest
from openeo_pg_parser_networkx.pg_schema import BoundingBox
from rio_tiler.models import ImageData

from titiler.openeo.stacapi import LoadCollection, stacApiBackend
from titiler.openeo.settings import ProcessingSettings


def test_processing_settings():
    """Test ProcessingSettings configuration."""
    # Test default value
    settings = ProcessingSettings()
    assert settings.max_pixels == 100_000_000

    # Test custom value via environment variable
    settings = ProcessingSettings(max_pixels=50_000_000)
    assert settings.max_pixels == 50_000_000


def test_load_collection_pixel_threshold(monkeypatch):
    """Test pixel threshold in load_collection."""
    # Mock SimpleSTACReader to return fixed dimensions
    class MockReader:
        def __init__(self, *args, **kwargs):
            self.crs = "EPSG:4326"
            self.width = 5000
            self.height = 5000

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def part(self, bbox, **kwargs):
            """Mock part method returning ImageData."""
            import numpy
            return ImageData(
                numpy.zeros((1, 100, 100), dtype="uint8"),
                assets=kwargs.get("assets", ["B01"]),
                crs=self.crs,
            )

    # Mock STAC item
    mock_item = {
        "type": "Feature",
        "id": "test-item",
        "bbox": [0, 0, 1, 1],
        "geometry": {
            "type": "Polygon",
            "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]
        },
        "properties": {
            "datetime": "2021-01-01T00:00:00Z"
        },
        "assets": {
            "B01": {
                "href": "https://example.com/B01.tif",
                "type": "image/tiff; application=geotiff"
            }
        }
    }

    # Mock _get_items to return our test item
    def mock_get_items(*args, **kwargs):
        return [mock_item]

    monkeypatch.setattr("titiler.openeo.stacapi.SimpleSTACReader", MockReader)
    monkeypatch.setattr(LoadCollection, "_get_items", mock_get_items)

    # Create test instance with smaller threshold
    settings = ProcessingSettings(max_pixels=10_000_000)  # 10 million pixels
    monkeypatch.setattr("titiler.openeo.stacapi.processing_settings", settings)

    backend = stacApiBackend(url="https://example.com")
    loader = LoadCollection(stac_api=backend)

    # Test with dimensions exceeding threshold
    with pytest.raises(ValueError) as exc_info:
        loader.load_collection(
            id="test",
            spatial_extent=BoundingBox(
                west=0, south=0, east=1, north=1, crs="EPSG:4326"
            ),
            width=5000,
            height=5000,
        )
    assert "Estimated output size too large" in str(exc_info.value)
    assert "max allowed: 10000000" in str(exc_info.value)

    # Test with acceptable dimensions
    result = loader.load_collection(
        id="test",
        spatial_extent=BoundingBox(
            west=0, south=0, east=1, north=1, crs="EPSG:4326"
        ),
        width=1000,
        height=1000,
    )
    assert isinstance(result, dict)
    assert len(result) > 0  # Should contain at least one date-keyed entry
    # First value should be ImageData
    first_value = next(iter(result.values()))
    assert isinstance(first_value, ImageData)


def test_load_collection_and_reduce_pixel_threshold(monkeypatch):
    """Test pixel threshold in load_collection_and_reduce."""
    # Mock SimpleSTACReader to return fixed dimensions
    class MockReader:
        def __init__(self, *args, **kwargs):
            self.crs = "EPSG:4326"
            self.width = 5000
            self.height = 5000

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def part(self, bbox, **kwargs):
            """Mock part method returning ImageData."""
            import numpy
            return ImageData(
                numpy.zeros((1, 100, 100), dtype="uint8"),
                assets=kwargs.get("assets", ["B01"]),
                crs=self.crs,
            )

    # Mock STAC item
    mock_item = {
        "type": "Feature",
        "id": "test-item",
        "bbox": [0, 0, 1, 1],
        "geometry": {
            "type": "Polygon",
            "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]
        },
        "properties": {
            "datetime": "2021-01-01T00:00:00Z"
        },
        "assets": {
            "B01": {
                "href": "https://example.com/B01.tif",
                "type": "image/tiff; application=geotiff"
            }
        }
    }

    # Mock _get_items to return our test item
    def mock_get_items(*args, **kwargs):
        return [mock_item]

    monkeypatch.setattr("titiler.openeo.stacapi.SimpleSTACReader", MockReader)
    monkeypatch.setattr(LoadCollection, "_get_items", mock_get_items)

    # Create test instance with smaller threshold
    settings = ProcessingSettings(max_pixels=10_000_000)  # 10 million pixels
    monkeypatch.setattr("titiler.openeo.stacapi.processing_settings", settings)

    backend = stacApiBackend(url="https://example.com")
    loader = LoadCollection(stac_api=backend)

    # Test with dimensions exceeding threshold
    with pytest.raises(ValueError) as exc_info:
        loader.load_collection_and_reduce(
            id="test",
            spatial_extent=BoundingBox(
                west=0, south=0, east=1, north=1, crs="EPSG:4326"
            ),
            width=5000,
            height=5000,
        )
    assert "Estimated output size too large" in str(exc_info.value)
    assert "max allowed: 10000000" in str(exc_info.value)

    # Test with acceptable dimensions
    result = loader.load_collection_and_reduce(
        id="test",
        spatial_extent=BoundingBox(
            west=0, south=0, east=1, north=1, crs="EPSG:4326"
        ),
        width=1000,
        height=1000,
    )
    assert isinstance(result, ImageData)
    assert result.data.shape[0] == 1  # Single band
    assert result.crs == "EPSG:4326"
