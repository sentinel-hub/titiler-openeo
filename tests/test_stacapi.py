"""Test titiler.openeo.stacapi."""

from datetime import datetime

import pytest
from openeo_pg_parser_networkx.pg_schema import BoundingBox, TemporalInterval
from pystac import Item
from rio_tiler.models import ImageData

from titiler.openeo.errors import OutputLimitExceeded, ProcessParameterInvalid
from titiler.openeo.reader import SimpleSTACReader
from titiler.openeo.settings import ProcessingSettings
from titiler.openeo.stacapi import LoadCollection, LoadStac, stacApiBackend


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


def test_load_collection_carries_source_items_into_the_stack(monkeypatch):
    """load_collection must expose its source items via get_source_items().

    Processes that need genuinely per-item STAC metadata -- asset hrefs,
    properties -- can only get it from here: load_collection groups items by
    acquisition datetime and mosaics each group, so without this the source
    items are discarded at task-construction time and no downstream process can
    recover them. `sar_backscatter` depends on it for calibration LUTs and GCPs.

    This asserts the contract between stacapi's task metadata and
    RasterStack.get_source_items, which is what a unit test built on a
    hand-rolled stack shape cannot catch.
    """
    monkeypatch.setattr("titiler.openeo.reader.SimpleSTACReader", MockReader)

    items = [
        Item.from_dict(_stac_item_dict("2021-01-01T00:00:00Z")),
        Item.from_dict(_stac_item_dict("2021-02-01T00:00:00Z")),
    ]
    monkeypatch.setattr(LoadCollection, "_get_items", lambda self, *a, **k: items)

    backend = stacApiBackend(url="https://example.com")
    loader = LoadCollection(stac_api=backend)
    stack = loader.load_collection(
        id="test",
        spatial_extent=BoundingBox(west=0, south=0, east=1, north=1, crs="EPSG:4326"),
        width=64,
        height=64,
    )

    keys = list(stack.keys())
    assert len(keys) == 2
    for key in keys:
        source_items = stack.get_source_items(key)
        assert len(source_items) == 1
        # The real pystac.Item, with its assets reachable.
        assert source_items[0] in items
        assert "B01" in source_items[0].assets


def test_load_collection_groups_same_datetime_items_into_one_slice(monkeypatch):
    """Items sharing an acquisition datetime are mosaicked into a single slice.

    get_source_items then reports *all* of them, which is what lets a process
    detect the case rather than silently treating the mosaic as one item.
    """
    monkeypatch.setattr("titiler.openeo.reader.SimpleSTACReader", MockReader)

    same_dt = "2021-01-01T00:00:00Z"
    first = _stac_item_dict(same_dt)
    second = _stac_item_dict(same_dt)
    second["id"] = "item-second"
    items = [Item.from_dict(first), Item.from_dict(second)]
    monkeypatch.setattr(LoadCollection, "_get_items", lambda self, *a, **k: items)

    backend = stacApiBackend(url="https://example.com")
    loader = LoadCollection(stac_api=backend)
    stack = loader.load_collection(
        id="test",
        spatial_extent=BoundingBox(west=0, south=0, east=1, north=1, crs="EPSG:4326"),
        width=64,
        height=64,
    )

    keys = list(stack.keys())
    assert len(keys) == 1, "same-datetime items must collapse into one slice"
    assert len(stack.get_source_items(keys[0])) == 2


def _range_only_item_dict(start: str, end: str) -> dict:
    """A STAC item with ``"datetime": null`` and only a start/end range."""
    item = _stac_item_dict(start)
    item["properties"].update(datetime=None, start_datetime=start, end_datetime=end)
    return item


def _load_range_only_items(monkeypatch, *periods):
    """Run load_collection over one range-only item per ``(start, end)`` period."""
    items = []
    for i, (start, end) in enumerate(periods):
        item = _range_only_item_dict(start, end)
        item["id"] = f"tile-{i}"
        items.append(Item.from_dict(item))
    monkeypatch.setattr(LoadCollection, "_get_items", lambda self, *a, **k: items)

    loader = LoadCollection(stac_api=stacApiBackend(url="https://example.com"))
    return loader.load_collection(
        id="test",
        spatial_extent=BoundingBox(west=0, south=0, east=1, north=1, crs="EPSG:4326"),
        width=64,
        height=64,
    )


def test_load_collection_keys_range_only_items_by_start_datetime(monkeypatch):
    """Tiles of one composite with ``"datetime": null`` form one slice at their start.

    Regression for #472: these items failed with
    ``'NoneType' object has no attribute 'isoformat'``.
    """
    feb = ("2013-02-10T00:00:00Z", "2013-02-17T23:59:59Z")
    stack = _load_range_only_items(monkeypatch, feb, feb)
    assert stack.timestamps() == [datetime(2013, 2, 10)]


def test_load_collection_rejects_periods_sharing_a_start(monkeypatch):
    """Items that start together but cover different periods are not mosaicked.

    E.g. a seasonal and a yearly product both starting on 1 January.
    """
    with pytest.raises(ProcessParameterInvalid, match="different periods"):
        _load_range_only_items(
            monkeypatch,
            ("2016-01-01T00:00:00Z", "2016-03-31T23:59:59Z"),
            ("2016-01-01T00:00:00Z", "2016-12-31T23:59:59Z"),
        )


# A triangle over the lower-left half of the [0, 0, 1, 1] square.
_TRIANGLE = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "properties": {},
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[0, 0], [1, 0], [0, 1], [0, 0]]],
            },
        }
    ],
}


def test_load_collection_geojson_spatial_extent(monkeypatch):
    """A GeoJSON spatial_extent loads its bounding box and masks outside the polygons.

    Regression for #445: the parameter accepted only a bounding box.
    """
    import numpy

    monkeypatch.setattr("titiler.openeo.reader.SimpleSTACReader", MockReader)

    captured = {}

    def mock_get_items(self, *args, spatial_extent=None, **kwargs):
        captured["search_extent"] = spatial_extent
        return [Item.from_dict(_stac_item_dict("2021-01-01T00:00:00Z"))]

    def mock_mosaic_reader(items, reader, bbox, **kwargs):
        captured["read_bbox"] = list(bbox)
        return ImageData(
            numpy.ma.ones((1, 10, 10), dtype="float32"),
            crs=kwargs["dst_crs"],
            bounds=tuple(bbox),
        ), None

    monkeypatch.setattr(LoadCollection, "_get_items", mock_get_items)
    monkeypatch.setattr("titiler.openeo.stacapi.mosaic_reader", mock_mosaic_reader)

    loader = LoadCollection(stac_api=stacApiBackend(url="https://example.com"))
    stack = loader.load_collection(
        id="test", spatial_extent=_TRIANGLE, width=10, height=10, target_crs=4326
    )

    search_extent = captured["search_extent"]
    assert isinstance(search_extent, BoundingBox)
    assert (
        search_extent.west,
        search_extent.south,
        search_extent.east,
        search_extent.north,
    ) == (0, 0, 1, 1)
    # Lazy: nothing is read before the slice is used.
    assert "read_bbox" not in captured

    img = stack.first
    assert captured["read_bbox"] == [0, 0, 1, 1]
    mask = numpy.ma.getmaskarray(img.array)[0]
    # Pixel centres below the diagonal are inside the triangle.
    assert not mask[9, 0]
    assert mask[0, 9]
    assert mask[numpy.triu_indices(10, k=1)].all()
    assert not mask[numpy.tril_indices(10, k=-1)].any()


@pytest.mark.parametrize(
    "geojson",
    [
        {"type": "Point", "coordinates": [0, 0]},
        {"type": "FeatureCollection", "features": []},
        {"type": "Polygon", "coordinates": []},
    ],
)
def test_load_collection_geojson_spatial_extent_invalid(monkeypatch, geojson):
    """A GeoJSON without Polygon or MultiPolygon geometry is an invalid parameter."""
    from titiler.openeo.errors import ProcessParameterInvalid

    monkeypatch.setattr(
        LoadCollection,
        "_get_items",
        lambda self, *a, **k: pytest.fail("no search for an invalid extent"),
    )
    loader = LoadCollection(stac_api=stacApiBackend(url="https://example.com"))
    with pytest.raises(ProcessParameterInvalid, match="spatial_extent"):
        loader.load_collection(id="test", spatial_extent=geojson)


@pytest.mark.parametrize(
    "value,expected_type",
    [
        ({"west": 0, "south": 0, "east": 1, "north": 1}, BoundingBox),
        (_TRIANGLE, dict),
    ],
)
def test_spatial_extent_parameter_reference(value, expected_type):
    """A bounding box from a parameter becomes a BoundingBox; GeoJSON stays a dict."""
    from typing import Any, Dict, Optional, Union

    from openeo_pg_parser_networkx.pg_schema import ParameterReference

    from titiler.openeo.processes.implementations.core import process

    @process
    def load(spatial_extent: Optional[Union[BoundingBox, Dict[str, Any]]] = None):
        return spatial_extent

    result = load(
        spatial_extent=ParameterReference(from_parameter="extent"),
        named_parameters={"extent": value},
    )
    assert isinstance(result, expected_type)


def test_load_stac_geojson_spatial_extent_single_item(monkeypatch):
    """load_stac on a single item reads the bounding box and masks outside the polygons."""
    import numpy

    monkeypatch.setattr(
        LoadStac,
        "_load_stac_object",
        lambda self, url: Item.from_dict(_stac_item_dict("2021-01-01T00:00:00Z")),
    )

    captured = {}

    def mock_reader(item, bbox, **kwargs):
        captured["read_bbox"] = list(bbox)
        return ImageData(
            numpy.ma.ones((1, 10, 10), dtype="float32"),
            crs=kwargs["dst_crs"],
            bounds=tuple(bbox),
        )

    monkeypatch.setattr("titiler.openeo.stacapi._reader", mock_reader)

    stack = LoadStac().load_stac(
        url="https://example.com/item.json",
        spatial_extent=_TRIANGLE,
        width=10,
        height=10,
    )

    img = stack.first
    assert captured["read_bbox"] == [0, 0, 1, 1]
    mask = numpy.ma.getmaskarray(img.array)[0]
    assert mask[numpy.triu_indices(10, k=1)].all()
    assert not mask[numpy.tril_indices(10, k=-1)].any()


_FEB_10_17 = {
    "datetime": None,
    "start_datetime": "2013-02-10T00:00:00Z",
    "end_datetime": "2013-02-17T23:59:59Z",
}


@pytest.mark.parametrize(
    "props,extent,kept",
    [
        (_FEB_10_17, ["2013-02-01", "2013-03-01"], True),
        # Overlap, as in a STAC API search: kept although it starts before the extent.
        (_FEB_10_17, ["2013-02-12", "2013-02-20"], True),
        (_FEB_10_17, ["2013-02-18", "2013-03-01"], False),
        # The extent end is excluded.
        (_FEB_10_17, ["2013-01-01", "2013-02-10"], False),
        # Offsets are compared as times, not as text.
        (
            {"datetime": None, "start_datetime": "2013-02-01T00:00:00+00:00"},
            ["2013-02-01", "2013-03-01"],
            True,
        ),
        (
            {"datetime": "2013-03-01T00:00:00+00:00"},
            ["2013-02-01", "2013-03-01"],
            False,
        ),
        # No date at all: skipped.
        ({"datetime": None}, ["2013-02-01", "2013-03-01"], False),
    ],
)
def test_load_stac_temporal_filter_keeps_items_overlapping_the_extent(
    props, extent, kept
):
    """load_stac keeps an item if its datetime or start/end range overlaps the extent."""
    item = {"properties": props}
    filtered = LoadStac()._filter_by_temporal_extent([item], TemporalInterval(extent))
    assert filtered == ([item] if kept else [])


def test_load_stac_passes_geojson_to_collection(monkeypatch):
    """For a Collection or Catalog, load_collection gets the GeoJSON itself."""
    captured = {}

    def mock_handle(self, stac_obj, spatial_extent=None, **kwargs):
        captured["spatial_extent"] = spatial_extent
        return "stack"

    import pystac

    collection = pystac.Collection(
        id="c",
        description="d",
        extent=pystac.Extent(
            pystac.SpatialExtent([[0, 0, 1, 1]]),
            pystac.TemporalExtent([[None, None]]),
        ),
    )
    monkeypatch.setattr(LoadStac, "_load_stac_object", lambda self, url: collection)
    monkeypatch.setattr(LoadStac, "_handle_collection_or_catalog", mock_handle)

    LoadStac().load_stac(url="https://example.com/c.json", spatial_extent=_TRIANGLE)
    assert captured["spatial_extent"] is _TRIANGLE


def test_load_stac_geojson_spatial_extent_invalid(monkeypatch):
    """An invalid GeoJSON fails before the STAC object is read, and names load_stac."""
    from titiler.openeo.errors import ProcessParameterInvalid

    monkeypatch.setattr(
        LoadStac,
        "_load_stac_object",
        lambda self, url: pytest.fail("no read for an invalid extent"),
    )
    with pytest.raises(ProcessParameterInvalid, match="'load_stac'"):
        LoadStac().load_stac(
            url="https://example.com/item.json",
            spatial_extent={"type": "Point", "coordinates": [0, 0]},
        )
