"""Test titiler.openeo.stacapi."""

import pytest
from pystac import Item
from rio_tiler.models import ImageData

from titiler.openeo.errors import OutputLimitExceeded
from titiler.openeo.models.openapi import SpatialExtent
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
                "proj:transform": [0.0002, 0.0, 0.0, 0.0, -0.0002, 0.0],
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
            spatial_extent=SpatialExtent(
                west=0, south=0, east=1, north=1, crs="EPSG:4326"
            ),
            bands=["B01"],
            width=15000,
            height=15000,
        )

    # Test with acceptable dimensions
    result = loader.load_collection(
        id="test",
        spatial_extent=SpatialExtent(west=0, south=0, east=1, north=1, crs="EPSG:4326"),
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
                "proj:transform": [0.0002, 0.0, 0.0, 0.0, -0.0002, 0.0],
                "proj:shape": [5000, 5000],  # Mocked dimensions
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

    monkeypatch.setattr("titiler.openeo.reader.SimpleSTACReader", MockReader)
    monkeypatch.setattr(LoadCollection, "_get_items", mock_get_items)

    # Create test instance with smaller threshold
    settings = ProcessingSettings(max_pixels=10_000_000)  # 10 million pixels
    monkeypatch.setattr("titiler.openeo.stacapi.processing_settings", settings)

    backend = stacApiBackend(url="https://example.com")
    loader = LoadCollection(stac_api=backend)

    # Test with dimensions exceeding threshold
    with pytest.raises(OutputLimitExceeded):
        loader.load_collection_and_reduce(
            id="test",
            spatial_extent=SpatialExtent(
                west=0, south=0, east=1, north=1, crs="EPSG:4326"
            ),
            width=15000,
            height=15000,
        )

    # Test with acceptable dimensions
    result = loader.load_collection_and_reduce(
        id="test",
        spatial_extent=SpatialExtent(west=0, south=0, east=1, north=1, crs="EPSG:4326"),
        width=1000,
        height=1000,
    )
    assert isinstance(result, dict)
    # FIrst value should be ImageData
    first_value = next(iter(result.values()))
    assert isinstance(first_value, ImageData)
    assert first_value.data.shape[0] == 1  # Single band
    assert first_value.crs == "EPSG:4326"


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
    spatial_extent = SpatialExtent(west=0, south=0, east=1, north=1, crs="EPSG:4326")

    # Call without explicit width/height to trigger resolution-based calculation
    loader.load_collection(
        id="test",
        spatial_extent=spatial_extent,
        width=None,
        height=None,
    )

    # Check that calculated dimensions are close to expected (1000x1000)
    # Allow some flexibility due to rounding
    width = captured_params.get("width", 0)
    height = captured_params.get("height", 0)
    assert 950 <= width <= 1050, f"Width {width} is not within expected range"
    assert 950 <= height <= 1050, f"Height {height} is not within expected range"
