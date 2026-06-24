"""Tests for the virtual bands plugin mechanism."""

from typing import List

import numpy
import pytest
from openeo_pg_parser_networkx.pg_schema import BoundingBox
from pystac import Item
from rio_tiler.models import ImageData

from titiler.openeo.reader import SimpleSTACReader
from titiler.openeo.stacapi import LoadCollection, stacApiBackend
from titiler.openeo.virtualbands import (
    BandMetadata,
    VirtualBandPlugin,
    VirtualBandRegistry,
)


def _item(assets, properties=None):
    """Build a minimal projected STAC item with the given asset names."""
    props = {
        "datetime": "2021-01-01T00:00:00Z",
        "proj:crs": "EPSG:4326",
        "proj:transform": [0.0002, 0.0, 0.0, 0.0, -0.0002, 0.0],
    }
    props.update(properties or {})
    return Item.from_dict(
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
            "properties": props,
            "assets": {
                name: {
                    "href": f"https://example.com/{name}.tif",
                    "type": "image/tiff; application=geotiff",
                }
                for name in assets
            },
            "links": [],
        }
    )


class _MockReader(SimpleSTACReader):
    def part(self, bbox, **kwargs):
        return ImageData(
            numpy.zeros(
                (1, kwargs.get("height", 4), kwargs.get("width", 4)), dtype="uint8"
            ),
            crs=kwargs.get("dst_crs", "EPSG:4326"),
        )


def _mosaic_mock(value_map, calls=None):
    """Return a mosaic_reader replacement filling each asset with a known value."""

    def _m(items, reader, bbox, **kwargs):
        assets = kwargs["assets"]
        if calls is not None:
            calls.append(list(assets))
        h = kwargs.get("height") or 4
        w = kwargs.get("width") or 4
        arr = numpy.ma.stack(
            [numpy.full((h, w), value_map[a], dtype="float32") for a in assets]
        )
        return ImageData(arr, crs=kwargs.get("dst_crs", "EPSG:4326")), None

    return _m


# --------------------------------------------------------------------------- #
# Registry
# --------------------------------------------------------------------------- #


def test_registry_from_config_resolves_entry_point():
    reg = VirtualBandRegistry.from_config(
        {
            "col": [
                {
                    "plugin": "normalized_difference",
                    "options": {"name": "NDVI", "a": "B08", "b": "B04"},
                }
            ]
        }
    )
    assert reg.has_plugins("col")
    assert reg.virtual_band_names("col") == ["NDVI"]
    assert not reg.has_plugins("other")


def test_registry_unknown_plugin_raises():
    with pytest.raises(ValueError, match="Unknown virtual band plugin"):
        VirtualBandRegistry.from_config({"col": [{"plugin": "does-not-exist"}]})


def test_registry_empty():
    reg = VirtualBandRegistry.empty()
    assert not reg.has_plugins("col")
    assert reg.virtual_band_names("col") == []


def test_registry_malformed_entry_raises():
    with pytest.raises(ValueError, match="must be an object with a 'plugin' key"):
        VirtualBandRegistry.from_config({"col": ["not-a-dict"]})


def test_registry_entries_must_be_a_list():
    with pytest.raises(ValueError, match="must be a list of plugin entries"):
        VirtualBandRegistry.from_config({"col": {"plugin": "normalized_difference"}})


def test_registry_options_must_be_object():
    with pytest.raises(ValueError, match="'options' for plugin"):
        VirtualBandRegistry.from_config(
            {"col": [{"plugin": "normalized_difference", "options": ["a", "b"]}]}
        )


def test_registry_rejects_duplicate_band_names():
    with pytest.raises(ValueError, match="Duplicate virtual band name 'NDVI'"):
        VirtualBandRegistry.from_config(
            {
                "col": [
                    {
                        "plugin": "normalized_difference",
                        "options": {"name": "NDVI", "a": "B08", "b": "B04"},
                    },
                    {
                        "plugin": "normalized_difference",
                        "options": {"name": "NDVI", "a": "B05", "b": "B04"},
                    },
                ]
            }
        )


def test_registry_split_preserves_order_and_collects_support():
    reg = VirtualBandRegistry.from_config(
        {
            "col": [
                {
                    "plugin": "normalized_difference",
                    "options": {"name": "NDVI", "a": "B08", "b": "B04"},
                }
            ]
        }
    )
    split = reg.split("col", ["SCL", "NDVI", "B02"])
    assert split.real == ["SCL", "B02"]
    assert split.virtual == ["NDVI"]
    assert split.support == ["B08", "B04"]


def test_registry_split_no_support_when_band_already_requested():
    reg = VirtualBandRegistry.from_config(
        {
            "col": [
                {
                    "plugin": "normalized_difference",
                    "options": {"name": "NDVI", "a": "B08", "b": "B04"},
                }
            ]
        }
    )
    split = reg.split("col", ["B08", "NDVI", "B04"])
    assert split.support == []


# --------------------------------------------------------------------------- #
# Metadata augmentation
# --------------------------------------------------------------------------- #


def _backend_with_ndvi():
    reg = VirtualBandRegistry.from_config(
        {
            "col": [
                {
                    "plugin": "normalized_difference",
                    "options": {"name": "NDVI", "a": "B08", "b": "B04"},
                }
            ]
        }
    )
    return stacApiBackend(url="https://example.com", virtual_bands=reg)


def test_augment_appends_to_existing_band_dimension():
    backend = _backend_with_ndvi()
    col = {"id": "col", "cube:dimensions": {"b": {"type": "bands", "values": ["SCL"]}}}
    backend._augment_with_virtual_bands(col)
    assert col["cube:dimensions"]["b"]["values"] == ["SCL", "NDVI"]


def test_augment_creates_band_dimension_when_missing():
    backend = _backend_with_ndvi()
    col = {"id": "col", "cube:dimensions": {}}
    backend._augment_with_virtual_bands(col)
    assert col["cube:dimensions"]["spectral"] == {"type": "bands", "values": ["NDVI"]}


def test_augment_noop_without_plugins():
    backend = stacApiBackend(url="https://example.com")
    col = {"id": "col", "cube:dimensions": {"b": {"type": "bands", "values": ["SCL"]}}}
    backend._augment_with_virtual_bands(col)
    assert col["cube:dimensions"]["b"]["values"] == ["SCL"]


# --------------------------------------------------------------------------- #
# Read path: band-math (NDVI) and ordering
# --------------------------------------------------------------------------- #


def test_load_collection_computes_ndvi_and_preserves_order(monkeypatch):
    reg = VirtualBandRegistry.from_config(
        {
            "col": [
                {
                    "plugin": "normalized_difference",
                    "options": {"name": "NDVI", "a": "B08", "b": "B04"},
                }
            ]
        }
    )
    item = _item(["SCL", "B08", "B04"])
    monkeypatch.setattr("titiler.openeo.reader.SimpleSTACReader", _MockReader)
    monkeypatch.setattr(LoadCollection, "_get_items", lambda *a, **k: [item])
    monkeypatch.setattr(
        "titiler.openeo.stacapi.mosaic_reader",
        _mosaic_mock({"SCL": 1.0, "B08": 0.6, "B04": 0.2}),
    )

    loader = LoadCollection(stac_api=stacApiBackend(url="https://x", virtual_bands=reg))
    result = loader.load_collection(
        id="col",
        spatial_extent=BoundingBox(west=0, south=0, east=1, north=1, crs="EPSG:4326"),
        bands=["SCL", "NDVI"],
        width=4,
        height=4,
    )

    assert result.band_names == ["SCL", "NDVI"]
    img = next(iter(result.values()))
    assert img.band_names == ["SCL", "NDVI"]
    assert img.array.shape[0] == 2
    # SCL passthrough, NDVI = (0.6 - 0.2) / (0.6 + 0.2) = 0.5; support bands dropped
    numpy.testing.assert_allclose(img.array[0], 1.0)
    numpy.testing.assert_allclose(img.array[1], 0.5, rtol=1e-5)


def test_load_collection_property_virtual_band(monkeypatch):
    reg = VirtualBandRegistry.from_config(
        {
            "col": [
                {
                    "plugin": "constant_from_property",
                    "options": {
                        "name": "viewZenithMean",
                        "property": "view:incidence_angle",
                    },
                }
            ]
        }
    )
    item = _item(["SCL"], properties={"view:incidence_angle": 7.5})
    monkeypatch.setattr("titiler.openeo.reader.SimpleSTACReader", _MockReader)
    monkeypatch.setattr(LoadCollection, "_get_items", lambda *a, **k: [item])
    monkeypatch.setattr(
        "titiler.openeo.stacapi.mosaic_reader", _mosaic_mock({"SCL": 1.0})
    )

    loader = LoadCollection(stac_api=stacApiBackend(url="https://x", virtual_bands=reg))
    result = loader.load_collection(
        id="col",
        spatial_extent=BoundingBox(west=0, south=0, east=1, north=1, crs="EPSG:4326"),
        bands=["SCL", "viewZenithMean"],
        width=4,
        height=4,
    )
    img = next(iter(result.values()))
    assert img.band_names == ["SCL", "viewZenithMean"]
    numpy.testing.assert_allclose(img.array[1], 7.5)


def test_load_collection_no_plugins_is_unchanged(monkeypatch):
    """Regression: a collection with no plugins reads exactly the requested bands."""
    calls: List[List[str]] = []
    item = _item(["B04", "B08"])
    monkeypatch.setattr("titiler.openeo.reader.SimpleSTACReader", _MockReader)
    monkeypatch.setattr(LoadCollection, "_get_items", lambda *a, **k: [item])
    monkeypatch.setattr(
        "titiler.openeo.stacapi.mosaic_reader",
        _mosaic_mock({"B04": 0.2, "B08": 0.6}, calls=calls),
    )

    loader = LoadCollection(stac_api=stacApiBackend(url="https://x"))
    result = loader.load_collection(
        id="col",
        spatial_extent=BoundingBox(west=0, south=0, east=1, north=1, crs="EPSG:4326"),
        bands=["B04", "B08"],
        width=4,
        height=4,
    )
    next(iter(result.values()))
    assert calls == [["B04", "B08"]]  # no support bands, exact order
    assert result.band_names == ["B04", "B08"]


# --------------------------------------------------------------------------- #
# Laziness
# --------------------------------------------------------------------------- #


class _CountingPlugin(VirtualBandPlugin):
    def __init__(self):
        super().__init__()
        self.calls = 0

    def provided_bands(self) -> List[BandMetadata]:
        return [BandMetadata(name="VBAND")]

    def required_bands(self) -> List[str]:
        return ["B04"]

    def compute(self, name, items, image):
        self.calls += 1
        return numpy.ma.zeros(image.array.shape[-2:], dtype="float32")


def test_virtual_band_not_computed_until_slice_materialized(monkeypatch):
    plugin = _CountingPlugin()
    reg = VirtualBandRegistry({"col": [plugin]})
    item = _item(["B04"])
    monkeypatch.setattr("titiler.openeo.reader.SimpleSTACReader", _MockReader)
    monkeypatch.setattr(LoadCollection, "_get_items", lambda *a, **k: [item])
    monkeypatch.setattr(
        "titiler.openeo.stacapi.mosaic_reader", _mosaic_mock({"B04": 0.2})
    )

    loader = LoadCollection(stac_api=stacApiBackend(url="https://x", virtual_bands=reg))
    result = loader.load_collection(
        id="col",
        spatial_extent=BoundingBox(west=0, south=0, east=1, north=1, crs="EPSG:4326"),
        bands=["VBAND"],
        width=4,
        height=4,
    )
    # Nothing computed at graph-build time.
    assert plugin.calls == 0
    # Computed once the slice is realized.
    next(iter(result.values()))
    assert plugin.calls == 1


class _BadShapePlugin(VirtualBandPlugin):
    def provided_bands(self) -> List[BandMetadata]:
        return [BandMetadata(name="BAD")]

    def required_bands(self) -> List[str]:
        return ["B04"]

    def compute(self, name, items, image):
        # Wrong shape: extra leading dimension.
        return numpy.ma.zeros((2, *image.array.shape[-2:]), dtype="float32")


def test_virtual_band_bad_shape_raises(monkeypatch):
    reg = VirtualBandRegistry({"col": [_BadShapePlugin()]})
    item = _item(["B04"])
    monkeypatch.setattr("titiler.openeo.reader.SimpleSTACReader", _MockReader)
    monkeypatch.setattr(LoadCollection, "_get_items", lambda *a, **k: [item])
    monkeypatch.setattr(
        "titiler.openeo.stacapi.mosaic_reader", _mosaic_mock({"B04": 0.2})
    )

    loader = LoadCollection(stac_api=stacApiBackend(url="https://x", virtual_bands=reg))
    result = loader.load_collection(
        id="col",
        spatial_extent=BoundingBox(west=0, south=0, east=1, north=1, crs="EPSG:4326"),
        bands=["B04", "BAD"],
        width=4,
        height=4,
    )
    with pytest.raises(ValueError, match="returned an array of shape"):
        next(iter(result.values()))


def test_purely_virtual_without_grid_anchor_raises(monkeypatch):
    """A purely property-derived band with no real band to anchor the grid errors."""
    reg = VirtualBandRegistry.from_config(
        {
            "col": [
                {
                    "plugin": "constant_from_property",
                    "options": {"name": "ANGLE", "property": "view:incidence_angle"},
                }
            ]
        }
    )
    item = _item(["SCL"], properties={"view:incidence_angle": 7.5})
    monkeypatch.setattr("titiler.openeo.reader.SimpleSTACReader", _MockReader)
    monkeypatch.setattr(LoadCollection, "_get_items", lambda *a, **k: [item])

    loader = LoadCollection(stac_api=stacApiBackend(url="https://x", virtual_bands=reg))
    with pytest.raises(ValueError, match="anchor the output grid"):
        loader.load_collection(
            id="col",
            spatial_extent=BoundingBox(
                west=0, south=0, east=1, north=1, crs="EPSG:4326"
            ),
            bands=["ANGLE"],
            width=4,
            height=4,
        )
