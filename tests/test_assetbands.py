"""Unit tests for titiler.openeo.assetbands.

`test_multiband_assets.py` covers the four call sites this module feeds
(discovery, summaries, read, resolution) against a real EOPF fixture; this
file is about the resolver itself -- the compatibility rules that keep
existing single-band catalogues untouched, and the precedence a resolved band
name must share with `SimpleSTACReader._get_options`.
"""

from typing import Any, Dict, List, Tuple

from titiler.openeo.assetbands import (
    asset_band_facts,
    resolve_asset_band,
    resolve_asset_bands,
)

Fact = Tuple[str, List[Dict[str, Any]]]

#: A stand-in for EOPF's `reflectance`: several bands, no rendering role. The
#: gate itself lives in `asset_band_facts`/`_bands_of` (tested separately
#: below); these fixtures already carry only the `bands` array that survives
#: it, since `resolve_asset_bands` is tested here in isolation from the gate.
MULTI_BAND_CUBE: Fact = (
    "reflectance",
    [
        {"name": "b02", "eo:common_name": "blue", "gsd": 10},
        {"name": "b01", "eo:common_name": "coastal", "gsd": 20},
    ],
)

SINGLE_BAND: Fact = ("B02_10m", [{"name": "B02"}])

NO_BANDS: Fact = ("AOT_10m", [])


def _facts(*entries):
    """Build `AssetBandFacts` the way `asset_band_facts` would, from raw
    (asset_key, bands) pairs already shaped like the module expects -- the
    rendering-role gate lives in `_bands_of`/`asset_band_facts`, so callers
    that already have the filtered `bands` array (as these tests do) bypass it
    intentionally, to test `resolve_asset_bands` in isolation."""
    return list(entries)


# ---------------------------------------------------------------------------
# asset_band_facts -- the rendering-role gate
# ---------------------------------------------------------------------------


def test_asset_band_facts_excludes_rendering_roles():
    """A multi-band asset tagged `visual` produces no facts -- the signal that
    distinguishes a TCI composite's fixed RGB channels from EOPF's
    `reflectance`, whose bands are independently addressable. A plain `bands`
    array with no rendering role is enough on its own; no further extension
    (e.g. the datacube extension's `cube:variables`) is required."""
    assets = {
        "reflectance": {
            "bands": [{"name": "b02"}, {"name": "b01"}],
            "roles": ["data", "reflectance"],
        },
        "TCI_10m": {
            "bands": [{"name": "B04"}, {"name": "B03"}, {"name": "B02"}],
            "roles": ["visual"],
        },
        "overview_10m": {
            "bands": [{"name": "B04"}, {"name": "B03"}, {"name": "B02"}],
            "roles": ["overview"],
        },
        "AOT_10m": {"bands": [{"name": "B02"}], "roles": ["data"]},
        "thumbnail": {"roles": ["thumbnail"]},
    }

    facts = dict(asset_band_facts(assets))

    assert [b["name"] for b in facts["reflectance"]] == ["b02", "b01"]
    assert facts["TCI_10m"] == []
    assert facts["overview_10m"] == []
    assert facts["AOT_10m"] == [{"name": "B02"}]
    assert facts["thumbnail"] == []


def test_asset_band_facts_accepts_pystac_assets():
    """`pystac.Asset` exposes `roles` as a first-class attribute, not inside
    `extra_fields` -- unlike a plain dict, where both live at the top level."""
    import pystac

    reflectance = pystac.Asset(
        href="https://example.com/reflectance",
        roles=["data", "reflectance"],
        extra_fields={"bands": [{"name": "b02"}, {"name": "b01"}]},
    )
    tci = pystac.Asset(
        href="https://example.com/tci",
        roles=["visual"],
        extra_fields={"bands": [{"name": "B04"}, {"name": "B03"}, {"name": "B02"}]},
    )
    facts = dict(asset_band_facts({"reflectance": reflectance, "TCI_10m": tci}))
    assert [b["name"] for b in facts["reflectance"]] == ["b02", "b01"]
    assert facts["TCI_10m"] == []


# ---------------------------------------------------------------------------
# resolve_asset_bands -- compatibility rules
# ---------------------------------------------------------------------------


def test_no_bands_asset_is_absent_from_the_result():
    resolved = resolve_asset_bands(_facts(NO_BANDS))
    assert resolved == {}


def test_single_band_asset_is_absent_from_the_result():
    """Keeps its asset key -- CDSE's `B02_10m` (holding a band named `B02`)
    must not be renamed to `B02`, which would collide with earth-search's own
    single-band asset key convention and rename saved graphs."""
    resolved = resolve_asset_bands(_facts(SINGLE_BAND))
    assert resolved == {}


def test_a_rendering_role_asset_is_absent():
    """Simulates a composite already filtered by the rendering-role gate --
    `resolve_asset_bands` itself does not re-check the gate, so this exercises
    the case only via an empty `bands` list, matching what
    `asset_band_facts` would hand it for a TCI-style asset."""
    resolved = resolve_asset_bands(_facts(("TCI_10m", [])))
    assert resolved == {}


def test_multi_band_cube_expands_every_band():
    resolved = resolve_asset_bands(_facts(MULTI_BAND_CUBE))

    assert set(resolved) == {"blue", "coastal"}
    assert resolved["blue"].asset_key == "reflectance"
    assert resolved["blue"].band_name == "blue"
    assert resolved["blue"].metadata["gsd"] == 10
    assert resolved["coastal"].metadata["gsd"] == 20


def test_a_mix_of_shapes_only_expands_the_multi_band_cube():
    resolved = resolve_asset_bands(_facts(MULTI_BAND_CUBE, SINGLE_BAND, NO_BANDS))
    assert set(resolved) == {"blue", "coastal"}


def test_colliding_band_names_across_two_assets_are_qualified():
    """Two multi-band assets that happen to publish the same band name must
    not have one silently shadow the other."""
    resolved = resolve_asset_bands(
        _facts(
            ("reflectance_a", [{"name": "b02"}, {"name": "b01"}]),
            ("reflectance_b", [{"name": "b02"}, {"name": "b03"}]),
        )
    )

    assert set(resolved) == {"reflectance_a_b02", "b01", "reflectance_b_b02", "b03"}
    assert resolved["reflectance_a_b02"].asset_key == "reflectance_a"
    assert resolved["reflectance_b_b02"].asset_key == "reflectance_b"
    # Unique names are left bare, so the common case is unaffected by a
    # collision happening elsewhere in the same item.
    assert resolved["b01"].asset_key == "reflectance_a"


def test_resolve_asset_band_singular_matches_the_plural_result():
    resolved = resolve_asset_band("blue", _facts(MULTI_BAND_CUBE))
    assert resolved is not None
    assert resolved.asset_key == "reflectance"

    assert resolve_asset_band("nope", _facts(MULTI_BAND_CUBE)) is None


# ---------------------------------------------------------------------------
# Naming precedence -- must match SimpleSTACReader._get_options exactly, or a
# resolved name is not one `_get_options` can itself look back up.
# ---------------------------------------------------------------------------


def test_eo_common_name_wins_over_name():
    resolved = resolve_asset_bands(
        _facts(
            (
                "reflectance",
                [{"name": "b02", "eo:common_name": "blue"}, {"name": "b01"}],
            )
        )
    )
    assert "blue" in resolved
    assert "b02" not in resolved


def test_legacy_common_name_wins_over_name_when_no_eo_prefix():
    resolved = resolve_asset_bands(
        _facts(
            (
                "reflectance",
                [{"name": "b02", "common_name": "blue"}, {"name": "b01"}],
            )
        )
    )
    assert "blue" in resolved
    assert "b02" not in resolved


def test_name_is_the_fallback_when_no_common_name_is_declared():
    resolved = resolve_asset_bands(
        _facts(("reflectance", [{"name": "b02"}, {"name": "b01"}]))
    )
    assert set(resolved) == {"b01", "b02"}


def test_a_band_with_no_name_at_all_is_skipped():
    """The asset still counts as multi-band by entry count (2 entries), so the
    well-formed one is not penalised for its sibling's missing `name`."""
    resolved = resolve_asset_bands(
        _facts(("reflectance", [{"name": "b02"}, {"gsd": 20}]))
    )
    assert set(resolved) == {"b02"}
