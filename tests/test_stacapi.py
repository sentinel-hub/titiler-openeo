"""Test titiler.openeo.stacapi."""

import pytest
from rio_tiler.models import ImageData

from titiler.openeo.models import SpatialExtent
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


def test_load_collection_pixel_threshold(monkeypatch):
    """Test pixel threshold in load_collection."""

    # Mock SimpleSTACReader to return fixed dimensions and transform
    class MockReader:
        def __init__(self, *args, **kwargs):
            import pyproj
            from affine import Affine

            self.crs = pyproj.CRS.from_epsg(4326)
            self.width = 5000
            self.height = 5000
            # Affine transform with 0.0002 degrees per pixel (typical for medium resolution imagery)
            self.transform = Affine(0.0002, 0.0, 0.0, 0.0, -0.0002, 0.0)

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
    with pytest.raises(ValueError) as exc_info:
        loader.load_collection(
            id="test",
            spatial_extent=SpatialExtent(
                west=0, south=0, east=1, north=1, crs="EPSG:4326"
            ),
            bands=["B01"],
            width=5000,
            height=5000,
        )
    assert "Estimated output size too large" in str(exc_info.value)
    assert "max allowed: 10000000" in str(exc_info.value)

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

    # Mock SimpleSTACReader to return fixed dimensions and transform
    class MockReader:
        def __init__(self, *args, **kwargs):
            import pyproj
            from affine import Affine

            self.crs = pyproj.CRS.from_epsg(4326)
            self.width = 5000
            self.height = 5000
            # Affine transform with 0.0002 degrees per pixel (typical for medium resolution imagery)
            self.transform = Affine(0.0002, 0.0, 0.0, 0.0, -0.0002, 0.0)

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
            "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]],
        },
        "properties": {"datetime": "2021-01-01T00:00:00Z"},
        "assets": {
            "B01": {
                "href": "https://example.com/B01.tif",
                "type": "image/tiff; application=geotiff",
            }
        },
    }

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
    with pytest.raises(ValueError) as exc_info:
        loader.load_collection_and_reduce(
            id="test",
            spatial_extent=SpatialExtent(
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

    # Mock SimpleSTACReader with specific transform (resolution)
    class MockReader:
        def __init__(self, *args, **kwargs):
            import pyproj
            from affine import Affine

            self.crs = pyproj.CRS.from_epsg(4326)
            self.width = 1000
            self.height = 1000
            # Affine transform with 0.001 degrees per pixel
            self.transform = Affine(0.001, 0.0, 0.0, 0.0, -0.001, 0.0)

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
            "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]],
        },
        "properties": {"datetime": "2021-01-01T00:00:00Z"},
        "assets": {
            "B01": {
                "href": "https://example.com/B01.tif",
                "type": "image/tiff; application=geotiff",
            }
        },
    }

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
    )

    # Check that calculated dimensions are close to expected (1000x1000)
    # Allow some flexibility due to rounding
    width = captured_params.get("width", 0)
    height = captured_params.get("height", 0)
    assert 950 <= width <= 1050, f"Width {width} is not within expected range"
    assert 950 <= height <= 1050, f"Height {height} is not within expected range"
