"""Tests for collection band summaries (issue #440).

openEO API 1.3.0: ``cube:dimensions`` is required, its band dimension gives the
band names, and the band fields in ``summaries`` describe the same bands in the
same order. ``stacApiBackend._sync_band_summaries`` rebuilds both
``summaries.bands`` (STAC 1.1) and ``summaries.eo:bands`` (eo extension v1.x)
from the band dimension. openEO Studio and the openEO Web Editor read
``summaries.bands`` for their band picker; the openEO Python client compares
``summaries.eo:bands`` with ``cube:dimensions``.
"""

import json
from pathlib import Path
from typing import Any, Dict, List

import pystac
import pytest

from titiler.openeo import stacapi
from titiler.openeo.stacapi import stacApiBackend

PC_FIXTURE = Path("tests/fixtures/sentinel2/collections/planetary_computer_full.json")


def _backend() -> stacApiBackend:
    return stacApiBackend(url="https://example.com")


def _openeo_collection(collection: pystac.Collection) -> Dict[str, Any]:
    """The pipeline `get_collection`/`get_collections` apply to one collection."""
    backend = _backend()
    collection = backend.add_version_if_missing(collection)
    collection = backend.add_data_cubes_if_missing(collection)
    col_dict = collection.to_dict()
    backend._fix_collection(col_dict)
    return col_dict


def _band_values(collection: Dict[str, Any]) -> List[str]:
    return next(
        dim["values"]
        for dim in collection["cube:dimensions"].values()
        if dim.get("type") == "bands"
    )


def _names(entries: List[Dict[str, Any]]) -> List[str]:
    return [entry["name"] for entry in entries]


@pytest.fixture
def pc_collection() -> pystac.Collection:
    """Planetary Computer's sentinel-2-l2a collection (trimmed, see the
    fixture README): 13 upstream eo:bands, a 3-band ``visual`` asset, band-less
    SCL/WVP/AOT assets and a granule-metadata asset for the angle bands."""
    return pystac.Collection.from_dict(json.loads(PC_FIXTURE.read_text()))


def test_planetary_computer_summaries_mirror_cube_dimensions(pc_collection):
    collection = _openeo_collection(pc_collection)
    summaries = collection["summaries"]
    values = _band_values(collection)

    assert _names(summaries["bands"]) == values
    assert _names(summaries["eo:bands"]) == values
    assert len(values) == len(set(values))
    for name in ("SCL", "WVP", "AOT", "sunZenithAngles", "viewZenithMean"):
        assert name in values
    # `visual` holds copies of the B04/B03/B02 assets, so its bands get the
    # asset key as a prefix (issue #440).
    for name in ("visual_red", "visual_green", "visual_blue", "visual_B04"):
        assert name in values
    assert "red" not in values


def test_planetary_computer_band_metadata(pc_collection):
    summaries = _openeo_collection(pc_collection)["summaries"]
    bands = {b["name"]: b for b in summaries["bands"]}
    eo_bands = {b["name"]: b for b in summaries["eo:bands"]}

    # Single-band asset: STAC 1.1 names in `bands`, eo v1.x names in `eo:bands`.
    assert bands["B04"] == {
        "name": "B04",
        "description": "Band 4 - Red - 10m",
        "eo:common_name": "red",
        "eo:center_wavelength": 0.665,
        "eo:full_width_half_max": 0.038,
        "gsd": 10,
    }
    assert eo_bands["B04"] == {
        "name": "B04",
        "description": "Band 4 - Red - 10m",
        "common_name": "red",
        "center_wavelength": 0.665,
        "full_width_half_max": 0.038,
    }

    # Band-less asset: name and a description from the asset title.
    assert bands["SCL"] == {
        "name": "SCL",
        "description": "Scene classfication map (SCL)",
        "gsd": 20,
    }
    assert eo_bands["SCL"] == {
        "name": "SCL",
        "description": "Scene classfication map (SCL)",
    }

    # Band of a multi-band asset: its own metadata, and a description that
    # says it is the `visual` asset's band, not the B04 asset.
    red = bands["visual_red"]
    assert red["eo:common_name"] == "red"
    assert red["eo:center_wavelength"] == 0.665
    assert "'visual'" in red["description"]
    assert "True color image" in red["description"]
    assert "not the 'B04' asset" in red["description"]

    # Derived band: a description from its band source.
    assert "Sun zenith angle" in bands["sunZenithAngles"]["description"]
    assert "viewing azimuth" in bands["viewAzimuthMean"]["description"]


def test_upstream_cube_dimensions_drive_the_summaries():
    """A collection that already has cube:dimensions upstream keeps them, and
    both summaries are rebuilt from them. Upstream band summaries give
    metadata by name, never the list."""
    collection = pystac.Collection.from_dict(
        {
            "type": "Collection",
            "stac_version": "1.1.0",
            "stac_extensions": [
                "https://stac-extensions.github.io/datacube/v2.2.0/schema.json"
            ],
            "id": "upstream-cube",
            "description": "Collection with cube:dimensions upstream",
            "license": "proprietary",
            "extent": {
                "spatial": {"bbox": [[0, 0, 1, 1]]},
                "temporal": {"interval": [[None, None]]},
            },
            "links": [],
            "cube:dimensions": {
                "bands": {"type": "bands", "values": ["nir", "B02", "mask"]},
            },
            "summaries": {
                "eo:bands": [
                    {"name": "B02", "common_name": "blue"},
                    {"name": "stale", "common_name": "red"},
                ],
                "bands": [
                    {"name": "nir", "eo:center_wavelength": 0.842},
                    {"name": "stale"},
                ],
            },
            "item_assets": {
                "B02": {
                    "type": "image/tiff",
                    "roles": ["data"],
                    "title": "Blue",
                    "eo:bands": [{"name": "B02", "center_wavelength": 0.49}],
                },
            },
        }
    )

    result = _openeo_collection(collection)
    summaries = result["summaries"]

    assert _band_values(result) == ["nir", "B02", "mask"]
    assert summaries["bands"] == [
        {"name": "nir", "eo:center_wavelength": 0.842},
        {
            "name": "B02",
            "description": "Blue",
            "eo:common_name": "blue",
            "eo:center_wavelength": 0.49,
        },
        {"name": "mask"},
    ]
    assert summaries["eo:bands"] == [
        {"name": "nir", "center_wavelength": 0.842},
        {
            "name": "B02",
            "description": "Blue",
            "common_name": "blue",
            "center_wavelength": 0.49,
        },
        {"name": "mask"},
    ]


def test_no_band_summaries_without_item_assets():
    collection = pystac.Collection(
        id="no-assets",
        description="No item_assets, no bands",
        extent=pystac.Extent(
            pystac.SpatialExtent([[0, 0, 1, 1]]),
            pystac.TemporalExtent([[None, None]]),
        ),
    )

    result = _openeo_collection(collection)

    assert "spectral" not in result["cube:dimensions"]
    summaries = result.get("summaries") or {}
    assert "bands" not in summaries
    assert "eo:bands" not in summaries


def test_every_resolution_variant_keeps_its_asset_key():
    """CDSE publishes one asset per band per resolution (B02_10m, B02_20m,
    B02_60m, all naming the band "B02"). Each one is a band of the cube under
    its asset key, so each one has its own summary entry."""
    collection = {
        "id": "sentinel-2-l2a",
        "cube:dimensions": {
            "spectral": {
                "type": "bands",
                "values": ["B02_10m", "B02_20m", "B02_60m"],
            },
        },
        "item_assets": {
            key: {
                "roles": ["data", "reflectance"],
                "gsd": gsd,
                "description": f"Blue (band 2) - {gsd}m",
                "eo:bands": [{"name": "B02", "common_name": "blue"}],
            }
            for key, gsd in (("B02_10m", 10), ("B02_20m", 20), ("B02_60m", 60))
        },
    }

    _backend()._fix_collection(collection)

    assert collection["summaries"]["bands"] == [
        {
            "name": f"B02_{gsd}m",
            "description": f"Blue (band 2) - {gsd}m",
            "eo:common_name": "blue",
            "gsd": gsd,
        }
        for gsd in (10, 20, 60)
    ]


def test_composite_asset_gets_no_spectral_fields():
    """A true-colour asset kept under its own key (role "visual") lists three
    bands; their spectral fields do not apply to the asset as a whole."""
    collection = {
        "id": "sentinel-2-l2a",
        "cube:dimensions": {"spectral": {"type": "bands", "values": ["TCI_10m"]}},
        "item_assets": {
            "TCI_10m": {
                "roles": ["visual"],
                "description": "True color image",
                "eo:bands": [{"name": "B04"}, {"name": "B03"}, {"name": "B02"}],
            },
        },
    }

    _backend()._fix_collection(collection)

    assert collection["summaries"]["bands"] == [
        {"name": "TCI_10m", "description": "True color image"},
    ]
    assert collection["summaries"]["eo:bands"] == [
        {"name": "TCI_10m", "description": "True color image"},
    ]


def test_fix_collection_still_normalizes_scalar_summaries():
    collection = {
        "id": "x",
        "summaries": {"processing:level": "L2A"},
        "cube:dimensions": {"spectral": {"type": "bands", "values": ["B02"]}},
    }

    _backend()._fix_collection(collection)

    assert collection["summaries"]["processing:level"] == ["L2A"]
    assert collection["summaries"]["bands"] == [{"name": "B02"}]


class _FakeClient:
    def __init__(self, collection: Dict[str, Any]):
        self._collection = collection

    def _load(self) -> pystac.Collection:
        return pystac.Collection.from_dict(json.loads(json.dumps(self._collection)))

    def get_collections(self):
        return [self._load()]

    def get_collection(self, collection_id: str):
        assert collection_id == self._collection["id"]
        return self._load()


def test_collections_endpoints_return_the_same_summaries(monkeypatch, app_no_auth):
    fake = _FakeClient(json.loads(PC_FIXTURE.read_text()))
    monkeypatch.setattr(stacApiBackend, "client", property(lambda self: fake))
    stacapi.collections_cache.clear()
    stacapi.collection_cache.clear()

    try:
        listed = app_no_auth.get("/collections")
        single = app_no_auth.get("/collections/sentinel-2-l2a")
    finally:
        stacapi.collections_cache.clear()
        stacapi.collection_cache.clear()

    assert listed.status_code == 200
    assert single.status_code == 200
    from_list = next(
        c for c in listed.json()["collections"] if c["id"] == "sentinel-2-l2a"
    )
    from_single = single.json()

    for collection in (from_list, from_single):
        values = _band_values(collection)
        assert _names(collection["summaries"]["bands"]) == values
        assert _names(collection["summaries"]["eo:bands"]) == values
        assert "SCL" in values

    assert from_list["summaries"]["bands"] == from_single["summaries"]["bands"]
    assert from_list["summaries"]["eo:bands"] == from_single["summaries"]["eo:bands"]
