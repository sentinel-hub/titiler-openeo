"""Multi-band assets end to end (titiler/openeo/assetbands.py), against a real
EOPF Sentinel-2 L2A item (tests/fixtures/eopf/items/sentinel2_l2a.json).

`test_assetbands.py` covers the resolver itself in isolation; this file covers
the four call sites it feeds, and -- the property that matters most -- that
discovery and the read path agree on every name.
"""

import json
from pathlib import Path

import pystac
import pytest
import rasterio

from titiler.openeo.reader import SimpleSTACReader, _get_cube_resolutions
from titiler.openeo.stacapi import stacApiBackend

FIXTURE = Path("tests/fixtures/eopf/items/sentinel2_l2a.json")

#: The band names `reflectance` publishes, keyed by their eo:common_name (the
#: name `_get_options` resolves them by -- see assetbands.py's docstring).
#: gsd matches the fixture: b01/b05/b06/b07/b11/b12/b8a are native 20m,
#: b02/b03/b04/b08 are native 10m, b09 is 60m.
EXPECTED_BANDS = {
    "coastal": 20,
    "blue": 10,
    "green": 10,
    "red": 10,
    "rededge071": 20,
    "rededge075": 20,
    "rededge078": 20,
    "nir": 10,
    "nir09": 60,
    "swir16": 20,
    "swir22": 20,
    "nir08": 20,
}

#: The item's other assets: single-band or band-less, so unaffected by any of
#: this -- kept as their own asset key.
UNCHANGED_ASSET_KEYS = {"AOT_10m", "SCL_20m", "WVP_10m"}


@pytest.fixture
def eopf_item() -> pystac.Item:
    return pystac.Item.from_dict(json.loads(FIXTURE.read_text()))


@pytest.fixture
def eopf_collection(eopf_item: pystac.Item) -> pystac.Collection:
    """A minimal collection whose item_assets mirror the fixture item's own
    assets -- the shape `getdimensions`/`_add_band_summaries` read."""
    collection = pystac.Collection(
        id=eopf_item.collection_id or "sentinel-2-l2a",
        description="EOPF Sentinel-2 L2A (test fixture)",
        extent=pystac.Extent(
            pystac.SpatialExtent([[0, 0, 1, 1]]),
            pystac.TemporalExtent([[None, None]]),
        ),
    )
    collection.extra_fields["item_assets"] = {
        key: asset.to_dict() for key, asset in eopf_item.assets.items()
    }
    collection.set_self_href("https://example.com/collection.json")
    return collection


# ---------------------------------------------------------------------------
# Discovery: getdimensions
# ---------------------------------------------------------------------------


def test_getdimensions_expands_reflectance_into_its_bands(eopf_collection):
    backend = stacApiBackend.__new__(stacApiBackend)
    dims = backend.getdimensions(eopf_collection)

    spectral = set(dims["spectral"].to_dict()["values"])

    assert spectral == set(EXPECTED_BANDS) | UNCHANGED_ASSET_KEYS
    assert "reflectance" not in spectral, "the asset key must not itself be advertised"


# ---------------------------------------------------------------------------
# Discovery: _add_band_summaries
# ---------------------------------------------------------------------------


def test_add_band_summaries_expands_reflectance_with_spectral_metadata(
    eopf_collection,
):
    collection = eopf_collection.to_dict()
    stacApiBackend._add_band_summaries(collection)

    summaries = {b["name"]: b for b in collection["summaries"]["bands"]}

    assert set(EXPECTED_BANDS) <= set(summaries)
    blue = summaries["blue"]
    assert blue["eo:common_name"] == "blue"
    assert blue["gsd"] == 10
    assert "eo:center_wavelength" in blue

    coastal = summaries["coastal"]
    assert coastal["gsd"] == 20


def test_add_band_summaries_leaves_bandless_assets_out(eopf_collection):
    """Pre-existing behaviour, unaffected by multi-band expansion:
    AOT_10m/SCL_20m/WVP_10m/thumbnail carry no `bands`/`eo:bands` at all, so
    they were never eligible for a summaries.bands entry."""
    collection = eopf_collection.to_dict()
    stacApiBackend._add_band_summaries(collection)

    names = {b["name"] for b in collection["summaries"]["bands"]}
    assert names.isdisjoint(UNCHANGED_ASSET_KEYS | {"reflectance", "thumbnail"})


# ---------------------------------------------------------------------------
# Read: SimpleSTACReader._get_asset_info
# ---------------------------------------------------------------------------


def test_get_asset_info_resolves_every_expanded_band(eopf_item):
    with SimpleSTACReader(eopf_item) as src:
        for name in EXPECTED_BANDS:
            info = src._get_asset_info(name)
            assert info["name"] == "reflectance"
            assert info["method_options"]["indexes"] == [
                i + 1
                for i, b in enumerate(
                    eopf_item.assets["reflectance"].extra_fields["bands"]
                )
                if (b.get("eo:common_name") or b.get("common_name") or b.get("name"))
                == name
            ]


def test_get_asset_info_still_serves_real_asset_keys_directly(eopf_item):
    with SimpleSTACReader(eopf_item) as src:
        info = src._get_asset_info("AOT_10m")
        assert info["name"] == "AOT_10m"
        assert "indexes" not in info["method_options"]


def test_get_asset_info_rejects_an_unknown_name_and_lists_every_alternative(
    eopf_item,
):
    with SimpleSTACReader(eopf_item) as src:
        with pytest.raises(Exception) as excinfo:
            src._get_asset_info("not-a-band")

        message = str(excinfo.value)
        assert "blue" in message
        assert "AOT_10m" in message


def test_derived_bands_still_win_over_inner_bands_on_a_name_collision(eopf_item):
    """`_get_asset_info` checks `_derived_bands` before `_inner_bands`
    (docs/adr/0002-band-sources.md), so a band-source rule that already
    resolved a specific annotation asset must not be shadowed by a same-named
    band living inside an unrelated multi-band asset. Forced via a direct
    assignment rather than a real band-source item, since the two mechanisms
    would not otherwise realistically collide on one item."""
    from titiler.openeo.bandsources import ResolvedBand

    with SimpleSTACReader(eopf_item) as src:
        src._derived_bands = {
            "blue": ResolvedBand(
                asset_key="AOT_10m",
                sibling_key=None,
                quantity=None,
                reader=src.reader,
            )
        }

        info = src._get_asset_info("blue")
        # `_get_derived_asset_info` names the AssetInfo after the requested
        # band ("blue"), but its `url` comes from the resolved annotation
        # asset -- AOT_10m's href, not reflectance's -- which is what proves
        # the derived-band branch ran instead of the inner-band one.
        aot_href = src.input.assets["AOT_10m"].get_absolute_href()
        reflectance_href = src.input.assets["reflectance"].get_absolute_href()
        assert info["url"].startswith(aot_href)
        assert not info["url"].startswith(reflectance_href)


# ---------------------------------------------------------------------------
# Resolution: _get_cube_resolutions / the gsd fallback in _get_asset_resolution
# ---------------------------------------------------------------------------


def test_cube_resolutions_use_each_bands_own_gsd(eopf_item):
    target_crs = rasterio.crs.CRS.from_epsg(32627)
    target_bbox = list(eopf_item.bbox)

    resolutions = _get_cube_resolutions(
        [eopf_item], target_crs, target_bbox, list(EXPECTED_BANDS)
    )
    (per_band,) = resolutions.values()

    for name, expected_gsd in EXPECTED_BANDS.items():
        (x_res, y_res, _bbox) = per_band[name][0]
        assert x_res == expected_gsd
        assert y_res == expected_gsd


def test_cube_resolutions_gsd_fallback_covers_assets_with_no_proj_transform(
    eopf_item,
):
    """AOT_10m/SCL_20m/WVP_10m carry only `gsd`, no `proj:transform` or
    `proj:shape` -- before the fallback in `_get_asset_resolution`, these
    silently contributed no resolution at all."""
    target_crs = rasterio.crs.CRS.from_epsg(32627)
    target_bbox = list(eopf_item.bbox)

    resolutions = _get_cube_resolutions(
        [eopf_item], target_crs, target_bbox, ["AOT_10m", "SCL_20m", "WVP_10m"]
    )
    (per_band,) = resolutions.values()

    assert per_band["AOT_10m"][0][:2] == (10.0, 10.0)
    assert per_band["SCL_20m"][0][:2] == (20.0, 20.0)
    assert per_band["WVP_10m"][0][:2] == (10.0, 10.0)


# ---------------------------------------------------------------------------
# The property that matters most: discovery and read must agree
# ---------------------------------------------------------------------------


def test_every_advertised_band_is_actually_readable(eopf_collection, eopf_item):
    """A backend that advertises a band name it cannot read is worse than one
    that advertises nothing -- the reason this module exists as one resolver
    shared by every call site, rather than four independent guesses."""
    backend = stacApiBackend.__new__(stacApiBackend)
    dims = backend.getdimensions(eopf_collection)
    advertised = set(dims["spectral"].to_dict()["values"])

    with SimpleSTACReader(eopf_item) as src:
        for name in advertised:
            src._get_asset_info(name)  # must not raise
