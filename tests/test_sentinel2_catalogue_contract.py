"""Catalogue-contract tests for Sentinel-2 view/sun angle band resolution
(docs/adr/0004-sentinel2-view-sun-angle-bands.md).

Table-driven over committed, trimmed real STAC items from the three
catalogues this project targets (`tests/fixtures/sentinel2/items/`, fetched
live 2026-08-07), mirroring `tests/test_sar_catalogue_contract.py`'s shape:
asserting all four bands resolve to the real `granule_metadata`/
`granule-metadata` asset (spelling differs by catalogue) with the real
raster band picked as sibling, for every real per-catalogue item shape.
Adding a catalogue means adding a row below and a fixture, per the ADR.

**What this does not verify: that Planetary Computer's asset is fetchable.**
It is not -- confirmed live, `HTTP 409 PublicAccessNotPermitted`
(ADR 0004 S1.3/S3.1). This file only proves `resolve_band` resolves
correctly against the real item *shape*; a live, credentialed fetch is a
separate concern this fixture-only test cannot and does not claim to cover.
"""

import json
from pathlib import Path

import pytest

from titiler.openeo.bandsources import BAND_SOURCES, resolve_band

FIXTURES = Path(__file__).parent / "fixtures" / "sentinel2" / "items"

_BANDS = (
    "viewZenithMean",
    "viewAzimuthMean",
    "sunZenithAngles",
    "sunAzimuthAngles",
)

#: expected_sibling is whichever 10m band sorts first alphabetically among
#: the fixture's own 10m bands (B02/B04/B08, blue/red/nir, B02/B04/B08) --
#: pick_nominal_sibling_by_resolution's documented tie-break, not a
#: "the red band specifically" claim.
CATALOGUES = [
    pytest.param("cdse.json", "granule_metadata", "B02_10m", id="CDSE"),
    pytest.param("earth_search.json", "granule_metadata", "blue", id="EarthSearch"),
    pytest.param(
        "planetary_computer.json", "granule-metadata", "B02", id="PlanetaryComputer"
    ),
]


def _load(fixture_name: str) -> dict:
    return json.loads((FIXTURES / fixture_name).read_text())


def _asset_facts(item: dict):
    return [
        (key, asset.get("type"), asset.get("roles", []))
        for key, asset in item["assets"].items()
    ]


def _sibling_candidates(item: dict):
    return [
        (key, asset.get("type"), asset.get("roles", []), asset.get("gsd"))
        for key, asset in item["assets"].items()
    ]


@pytest.mark.parametrize("fixture_name,metadata_key,expected_sibling", CATALOGUES)
def test_all_four_bands_resolve_for_every_catalogue(
    fixture_name, metadata_key, expected_sibling
):
    item = _load(fixture_name)
    assets = _asset_facts(item)
    sibling_candidates = _sibling_candidates(item)

    for band in _BANDS:
        resolved = resolve_band(
            "sentinel-2-l2a",
            band,
            assets,
            BAND_SOURCES,
            sibling_candidates=sibling_candidates,
        )
        assert resolved is not None, f"{band} did not resolve on {fixture_name}"
        assert resolved.asset_key == metadata_key
        assert resolved.sibling_key == expected_sibling


@pytest.mark.parametrize("fixture_name,metadata_key,_sibling", CATALOGUES)
def test_metadata_asset_present_with_the_expected_key_spelling(
    fixture_name, metadata_key, _sibling
):
    """Pins the underscore/hyphen spelling difference this feature depends
    on (`_GRANULE_METADATA_ASSET = re.compile(r"granule[_-]metadata")`) --
    a future re-fetch that silently normalizes it shouldn't quietly stop
    testing the case this fixture exists for."""
    item = _load(fixture_name)
    assert metadata_key in item["assets"]
    assert item["assets"][metadata_key]["type"] == "application/xml"


def test_cdse_and_earth_search_also_publish_view_properties():
    """Documents that the flattened view:* STAC properties this feature
    deliberately does NOT read (ADR 0004 S1.2) are present on CDSE/Earth
    Search's real items -- confirming the earlier, superseded design's
    property names were real, just the wrong layer to read from."""
    for fixture_name in ("cdse.json", "earth_search.json"):
        item = _load(fixture_name)
        assert "view:incidence_angle" in item["properties"]
        assert "view:azimuth" in item["properties"]


def test_planetary_computer_item_has_no_view_properties():
    """The gap this feature's design works around: PC's item properties
    carry no view:* at all -- reading the granule-metadata asset directly
    is what makes viewZenithMean/viewAzimuthMean possible there too (in
    principle; fetching it is a separate, currently unsupported concern,
    see the module docstring)."""
    item = _load("planetary_computer.json")
    assert not any(k.startswith("view:") for k in item["properties"])
