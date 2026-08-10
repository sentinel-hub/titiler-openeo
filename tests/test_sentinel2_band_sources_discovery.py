"""Tests for Sentinel-2 view/sun angle band discovery (docs/adr/0004-sentinel2-view-sun-angle-bands.md).

Mirrors tests/test_band_sources_discovery.py's shape: pure `derive_bands`
matching logic, then `stacApiBackend.getdimensions` end to end against real,
trimmed collection fixtures for the three catalogues this project targets.
"""

import json
from pathlib import Path

import pystac
import pytest

from titiler.openeo.bandsources import BAND_SOURCES, derive_bands
from titiler.openeo.bandsources.sentinel2_sources import BAND_SOURCES_S2
from titiler.openeo.stacapi import stacApiBackend

FIXTURES = Path(__file__).parent / "fixtures" / "sentinel2" / "collections"

CATALOGUES = [
    pytest.param("cdse.json", id="CDSE"),
    pytest.param("earth_search.json", id="EarthSearch"),
    pytest.param("planetary_computer.json", id="PlanetaryComputer"),
]

_EXPECTED_BANDS = (
    "viewZenithMean",
    "viewAzimuthMean",
    "sunZenithAngles",
    "sunAzimuthAngles",
)


def _load_collection(fixture_name: str) -> pystac.Collection:
    data = json.loads((FIXTURES / fixture_name).read_text())
    return pystac.Collection.from_dict(data)


def _backend() -> stacApiBackend:
    return stacApiBackend(url="https://example.com")


# --------------------------------------------------------------------------
# derive_bands -- pure matching logic, no pystac
# --------------------------------------------------------------------------


def test_derive_bands_matches_underscore_and_hyphen_granule_metadata():
    """CDSE/Earth Search spell the asset `granule_metadata`, Planetary
    Computer `granule-metadata` -- both must resolve to the same four bands."""
    for asset_key in ("granule_metadata", "granule-metadata"):
        assets = [(asset_key, "application/xml", ["metadata"])]
        bands = derive_bands("sentinel-2-l2a", assets, BAND_SOURCES_S2)
        assert set(bands) == set(_EXPECTED_BANDS)


def test_derive_bands_requires_matching_collection():
    assets = [("granule_metadata", "application/xml", ["metadata"])]
    assert derive_bands("sentinel-1-grd", assets, BAND_SOURCES_S2) == []


def test_derive_bands_requires_matching_media_type():
    assets = [("granule_metadata", "application/json", ["metadata"])]
    assert derive_bands("sentinel-2-l2a", assets, BAND_SOURCES_S2) == []


def test_derive_bands_unrelated_xml_metadata_asset_does_not_match():
    """product_metadata/datastrip_metadata are also application/xml +
    metadata but must not be mistaken for the granule-metadata asset."""
    assets = [
        ("product_metadata", "application/xml", ["metadata"]),
        ("datastrip_metadata", "application/xml", ["metadata"]),
    ]
    assert derive_bands("sentinel-2-l2a", assets, BAND_SOURCES_S2) == []


# --------------------------------------------------------------------------
# The shipped registry against real, trimmed catalogue fixtures
# --------------------------------------------------------------------------


@pytest.mark.parametrize("fixture_name", CATALOGUES)
def test_shipped_registry_derives_all_four_bands(fixture_name):
    collection = _load_collection(fixture_name)
    dims = _backend().getdimensions(collection)

    values = set(dims["spectral"].values)
    for band in _EXPECTED_BANDS:
        assert band in values


@pytest.mark.parametrize("fixture_name", CATALOGUES)
def test_derived_band_names_never_collide_with_real_asset_keys(fixture_name):
    collection = _load_collection(fixture_name)
    item_assets = collection.extra_fields["item_assets"]
    real_asset_keys = set(item_assets.keys())

    asset_facts = [
        (key, asset.get("type"), asset.get("roles") or [])
        for key, asset in item_assets.items()
    ]
    derived = set(derive_bands(collection.id, asset_facts, BAND_SOURCES))

    assert not (real_asset_keys & derived)


@pytest.mark.parametrize("fixture_name", CATALOGUES)
def test_measurement_bands_still_advertised(fixture_name):
    """The pre-existing behaviour (real raster assets with role `data`) must
    survive unchanged alongside the new derived bands."""
    collection = _load_collection(fixture_name)
    dims = _backend().getdimensions(collection)
    values = set(dims["spectral"].values)

    # Every fixture keeps at least one 10m band under its catalogue's own
    # naming convention.
    assert values & {"B02_10m", "blue", "B02"}


def test_cdse_product_asset_is_not_advertised_as_a_band():
    """Sentinel-2's CDSE `Product` asset, like Sentinel-1's, is
    application/zip and must not be advertised as a raster band."""
    collection = _load_collection("cdse.json")
    dims = _backend().getdimensions(collection)

    assert "Product" not in dims["spectral"].values


@pytest.mark.parametrize("fixture_name", CATALOGUES)
def test_spectral_dimension_values_are_sorted(fixture_name):
    collection = _load_collection(fixture_name)
    dims = _backend().getdimensions(collection)

    assert dims["spectral"].values == sorted(dims["spectral"].values)


def test_no_sentinel2_sources_match_when_registry_is_empty(monkeypatch):
    """Sentinel-2 bands contributing nothing must not affect the
    pre-existing role=data behaviour at all."""
    monkeypatch.setattr("titiler.openeo.stacapi.BAND_SOURCES", [])
    collection = _load_collection("cdse.json")

    dims = _backend().getdimensions(collection)
    values = set(dims["spectral"].values)

    assert "B02_10m" in values
    for band in _EXPECTED_BANDS:
        assert band not in values
