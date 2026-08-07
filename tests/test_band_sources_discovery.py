"""Tests for band-source discovery (issue #348, ADR 0002, increment 1).

Two layers: `registry.derive_bands` in isolation (pure, no pystac -- matching
logic only), and `stacApiBackend.getdimensions` end to end against real,
trimmed collection fixtures for the three catalogues this project targets.
"""

import json
import re
from pathlib import Path

import pystac
import pytest

from titiler.openeo.bandsources import BAND_SOURCES, BandSource, derive_bands
from titiler.openeo.stacapi import stacApiBackend

FIXTURES = Path(__file__).parent / "fixtures" / "sar" / "collections"

CATALOGUES = [
    pytest.param("cdse.json", id="CDSE"),
    pytest.param("earth_search.json", id="EarthSearch"),
    pytest.param("planetary_computer.json", id="PlanetaryComputer"),
]

#: The bands every catalogue's fixture must produce (ADR 0002 S2.5), for each
#: of the four polarisations present in every fixture's item_assets.
_EXPECTED_DERIVED_SUFFIXES = (
    "sigma0_lut",
    "beta0_lut",
    "gamma0_lut",
    "dn_lut",
    "ellipsoid_incidence_angle",
    "noise_lut",
)
_POLARISATIONS = ("hh", "hv", "vh", "vv")


def _load_collection(fixture_name: str) -> pystac.Collection:
    data = json.loads((FIXTURES / fixture_name).read_text())
    return pystac.Collection.from_dict(data)


def _backend() -> stacApiBackend:
    return stacApiBackend(url="https://example.com")


# --------------------------------------------------------------------------
# registry.derive_bands -- pure matching logic, no pystac
# --------------------------------------------------------------------------


def test_derive_bands_formats_named_groups_into_band_names():
    source = BandSource(
        collection=re.compile("my-collection"),
        media_types=frozenset({"application/xml"}),
        roles=frozenset({"metadata"}),
        asset=re.compile(r"noise-(?P<pol>[a-z]{2})"),
        bands=(("{pol}_noise_lut", "noise"),),
    )
    assets = [("noise-vv", "application/xml", ["metadata"])]

    assert derive_bands("my-collection", assets, [source]) == ["vv_noise_lut"]


def test_derive_bands_requires_matching_media_type():
    source = BandSource(
        collection=re.compile("my-collection"),
        media_types=frozenset({"application/xml"}),
        roles=frozenset({"metadata"}),
        asset=re.compile(r"noise-(?P<pol>[a-z]{2})"),
        bands=(("{pol}_noise_lut", "noise"),),
    )
    assets = [("noise-vv", "application/json", ["metadata"])]

    assert derive_bands("my-collection", assets, [source]) == []


def test_derive_bands_requires_matching_role():
    source = BandSource(
        collection=re.compile("my-collection"),
        media_types=frozenset({"application/xml"}),
        roles=frozenset({"metadata"}),
        asset=re.compile(r"noise-(?P<pol>[a-z]{2})"),
        bands=(("{pol}_noise_lut", "noise"),),
    )
    assets = [("noise-vv", "application/xml", ["data"])]

    assert derive_bands("my-collection", assets, [source]) == []


def test_derive_bands_asset_key_must_fullmatch():
    """A source's asset regex must match the whole key, not a substring --
    otherwise `schema-noise-vv` would also match a hypothetical
    `schema-noise-vv-extra` sibling asset."""
    source = BandSource(
        collection=re.compile("my-collection"),
        media_types=frozenset({"application/xml"}),
        roles=frozenset({"metadata"}),
        asset=re.compile(r"noise-(?P<pol>[a-z]{2})"),
        bands=(("{pol}_noise_lut", "noise"),),
    )
    assets = [("noise-vv-extra", "application/xml", ["metadata"])]

    assert derive_bands("my-collection", assets, [source]) == []


def test_derive_bands_requires_matching_collection():
    source = BandSource(
        collection=re.compile("sentinel-1-grd"),
        media_types=frozenset({"application/xml"}),
        roles=frozenset({"metadata"}),
        asset=re.compile(r"noise-(?P<pol>[a-z]{2})"),
        bands=(("{pol}_noise_lut", "noise"),),
    )
    assets = [("noise-vv", "application/xml", ["metadata"])]

    assert derive_bands("sentinel-2-l2a", assets, [source]) == []


def test_derive_bands_sorted_and_deduplicated():
    """Two sources contributing the same name must not duplicate it, and the
    result must be sorted regardless of match order (issue #280)."""
    sources = [
        BandSource(
            collection=re.compile("c"),
            media_types=frozenset({"application/xml"}),
            roles=frozenset({"metadata"}),
            asset=re.compile(r"z-(?P<pol>[a-z]{2})"),
            bands=(("{pol}_shared", "q1"),),
        ),
        BandSource(
            collection=re.compile("c"),
            media_types=frozenset({"application/xml"}),
            roles=frozenset({"metadata"}),
            asset=re.compile(r"a-(?P<pol>[a-z]{2})"),
            bands=(("{pol}_shared", "q1"), ("{pol}_only_here", "q2")),
        ),
    ]
    assets = [
        ("z-vv", "application/xml", ["metadata"]),
        ("a-vv", "application/xml", ["metadata"]),
    ]

    assert derive_bands("c", assets, sources) == ["vv_only_here", "vv_shared"]


def test_derive_bands_empty_roles_never_matches():
    source = BandSource(
        collection=re.compile("c"),
        media_types=frozenset({"application/xml"}),
        roles=frozenset({"metadata"}),
        asset=re.compile(r"noise-(?P<pol>[a-z]{2})"),
        bands=(("{pol}_noise_lut", "noise"),),
    )
    assets = [("noise-vv", "application/xml", [])]

    assert derive_bands("c", assets, [source]) == []


# --------------------------------------------------------------------------
# The shipped registry against real, trimmed catalogue fixtures
# --------------------------------------------------------------------------


@pytest.mark.parametrize("fixture_name", CATALOGUES)
def test_shipped_registry_derives_expected_bands(fixture_name):
    collection = _load_collection(fixture_name)
    dims = _backend().getdimensions(collection)

    values = set(dims["spectral"].values)

    for pol in _POLARISATIONS:
        for suffix in _EXPECTED_DERIVED_SUFFIXES:
            assert f"{pol}_{suffix}" in values


@pytest.mark.parametrize("fixture_name", CATALOGUES)
def test_manifest_and_schema_product_never_match(fixture_name):
    """schema-product-* and the SAFE manifest are application/xml + metadata,
    same as schema-calibration-*/schema-noise-*, but must not be mistaken for
    them -- the asset key is the only thing that discriminates these.

    Every fixture's item_assets carries schema-calibration-*, schema-noise-*
    AND schema-product-* for all four polarisations, plus a manifest asset --
    all application/xml + metadata. If schema-product-* or the manifest also
    matched a registry entry, the derived count below would be higher than
    exactly 6 bands per polarisation (4 LUT vectors + incidence angle from
    schema-calibration-*, + noise_lut from schema-noise-*).
    """
    collection = _load_collection(fixture_name)
    dims = _backend().getdimensions(collection)
    derived = set(dims["spectral"].values) - set(_POLARISATIONS)

    assert len(derived) == len(_POLARISATIONS) * len(_EXPECTED_DERIVED_SUFFIXES)


@pytest.mark.parametrize("fixture_name", CATALOGUES)
def test_derived_band_names_never_collide_with_real_asset_keys(fixture_name):
    """Abandon condition from the plan: derived names must never collide with
    an actual asset key on any target catalogue.

    Checked against `derive_bands` directly (not `getdimensions`'s merged
    output, which trivially contains real keys like `hh` from the pre-existing
    role=data behaviour and would make this check meaningless).
    """
    collection = _load_collection(fixture_name)
    item_assets = collection.extra_fields["item_assets"]
    real_asset_keys = set(item_assets.keys())

    asset_facts = [
        (key, asset.get("type"), asset.get("roles") or [])
        for key, asset in item_assets.items()
    ]
    derived = set(derive_bands(collection.id, asset_facts, BAND_SOURCES))

    assert not (real_asset_keys & derived)


def test_cdse_product_asset_is_not_advertised_as_a_band():
    """CDSE's `Product` asset is `application/zip` with role `data` among
    others -- it is not a raster, and must not be advertised as one."""
    collection = _load_collection("cdse.json")
    dims = _backend().getdimensions(collection)

    assert "Product" not in dims["spectral"].values


@pytest.mark.parametrize("fixture_name", CATALOGUES)
def test_measurement_bands_still_advertised(fixture_name):
    """The pre-existing behaviour (real raster assets with role `data`) must
    survive unchanged alongside the new derived bands."""
    collection = _load_collection(fixture_name)
    dims = _backend().getdimensions(collection)
    values = set(dims["spectral"].values)

    for pol in _POLARISATIONS:
        assert pol in values


@pytest.mark.parametrize("fixture_name", CATALOGUES)
def test_spectral_dimension_values_are_sorted(fixture_name):
    collection = _load_collection(fixture_name)
    dims = _backend().getdimensions(collection)

    assert dims["spectral"].values == sorted(dims["spectral"].values)


def test_collection_matching_no_registry_entry_is_unaffected():
    """A collection with real raster assets but nothing matching any
    band-source entry must behave exactly as before this change: only its
    role=data assets are advertised.

    Uses a collection id matching neither the Sentinel-1 nor the Sentinel-2
    registry entries -- an unrelated ``granule_metadata``-shaped asset is
    included to also prove that an arbitrary XML/metadata asset does not
    spuriously match a band source when the *collection* itself isn't a
    registry target."""
    collection = pystac.Collection.from_dict(
        {
            "type": "Collection",
            "stac_version": "1.0.0",
            "id": "some-other-optical-collection",
            "description": "test",
            "license": "proprietary",
            "extent": {
                "spatial": {"bbox": [[-180, -90, 180, 90]]},
                "temporal": {"interval": [["2015-01-01T00:00:00Z", None]]},
            },
            "links": [],
            "item_assets": {
                "B02": {
                    "type": "image/tiff; application=geotiff; profile=cloud-optimized",
                    "roles": ["data"],
                },
                "B03": {
                    "type": "image/tiff; application=geotiff; profile=cloud-optimized",
                    "roles": ["data"],
                },
                "granule_metadata": {
                    "type": "application/xml",
                    "roles": ["metadata"],
                },
            },
        }
    )

    dims = _backend().getdimensions(collection)

    assert set(dims["spectral"].values) == {"B02", "B03"}


def test_no_band_sources_match_when_registry_is_empty(monkeypatch):
    """derive_bands contributing nothing must not affect the pre-existing
    role=data behaviour at all."""
    monkeypatch.setattr("titiler.openeo.stacapi.BAND_SOURCES", [])
    collection = _load_collection("cdse.json")

    dims = _backend().getdimensions(collection)
    values = set(dims["spectral"].values)

    for pol in _POLARISATIONS:
        assert pol in values
    for suffix in _EXPECTED_DERIVED_SUFFIXES:
        assert f"vv_{suffix}" not in values
