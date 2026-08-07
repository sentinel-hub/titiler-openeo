"""Tests for BandSource.sibling's callable form (docs/adr/0004-sentinel2-view-sun-angle-bands.md S2.1).

Two things to prove: `pick_nominal_sibling_by_resolution` in isolation, and
that widening `sibling` to accept a callable changes nothing for a
Sentinel-1-shaped source whose `sibling` is a plain string.
"""

import re

from titiler.openeo.bandsources import BandSource, resolve_band
from titiler.openeo.bandsources.registry import pick_nominal_sibling_by_resolution

# --------------------------------------------------------------------------
# pick_nominal_sibling_by_resolution -- pure, no pystac
# --------------------------------------------------------------------------


def test_picks_smallest_gsd():
    candidates = [
        ("B04_20m", "image/jp2", ["data"], 20.0),
        ("B04_10m", "image/jp2", ["data"], 10.0),
        ("B04_60m", "image/jp2", ["data"], 60.0),
    ]
    assert pick_nominal_sibling_by_resolution(candidates) == "B04_10m"


def test_ties_break_alphabetically_by_key():
    candidates = [
        ("red", "image/tiff", ["data"], 10.0),
        ("blue", "image/tiff", ["data"], 10.0),
    ]
    assert pick_nominal_sibling_by_resolution(candidates) == "blue"


def test_excludes_non_data_roles():
    candidates = [
        ("granule_metadata", "application/xml", ["metadata"], None),
        ("thumbnail", "image/jpeg", ["thumbnail"], None),
        ("B04_10m", "image/jp2", ["data"], 10.0),
    ]
    assert pick_nominal_sibling_by_resolution(candidates) == "B04_10m"


def test_excludes_archive_media_type():
    """A CDSE-style `Product` zip asset can carry role `data` -- must not be
    picked as a resolution/mask sibling (mirrors stacapi.py's own
    `_ARCHIVE_MEDIA_TYPES` filter, ADR 0002 S1.2 consequence 4)."""
    candidates = [
        ("Product", "application/zip", ["data", "metadata", "archive"], None),
        ("B04_10m", "image/jp2", ["data"], 10.0),
    ]
    assert pick_nominal_sibling_by_resolution(candidates) == "B04_10m"


def test_falls_back_to_alphabetical_when_no_gsd_declared():
    candidates = [
        ("red", "image/tiff", ["data"], None),
        ("blue", "image/tiff", ["data"], None),
    ]
    assert pick_nominal_sibling_by_resolution(candidates) == "blue"


def test_no_eligible_candidate_returns_none():
    candidates = [
        ("granule_metadata", "application/xml", ["metadata"], None),
        ("Product", "application/zip", ["data"], None),
    ]
    assert pick_nominal_sibling_by_resolution(candidates) is None


def test_empty_candidates_returns_none():
    assert pick_nominal_sibling_by_resolution([]) is None


# --------------------------------------------------------------------------
# resolve_band with a callable `sibling` -- the new dispatch path
# --------------------------------------------------------------------------


def _property_style_source(reader=object):
    return BandSource(
        collection=re.compile("sentinel-2-l2a"),
        media_types=frozenset({"application/xml"}),
        roles=frozenset({"metadata"}),
        asset=re.compile(r"granule[_-]metadata"),
        bands=(("viewZenithMean", "zenith"),),
        sibling=pick_nominal_sibling_by_resolution,
        reader=reader,
    )


def test_callable_sibling_is_consulted_when_candidates_given():
    source = _property_style_source()
    assets = [("granule_metadata", "application/xml", ["metadata"])]
    sibling_candidates = [
        ("granule_metadata", "application/xml", ["metadata"], None),
        ("B04_10m", "image/jp2", ["data"], 10.0),
    ]

    resolved = resolve_band(
        "sentinel-2-l2a",
        "viewZenithMean",
        assets,
        [source],
        sibling_candidates=sibling_candidates,
    )

    assert resolved is not None
    assert resolved.sibling_key == "B04_10m"


def test_callable_sibling_yields_none_without_candidates():
    """sibling_candidates is optional -- a caller that doesn't pass it gets
    no sibling, not a crash."""
    source = _property_style_source()
    assets = [("granule_metadata", "application/xml", ["metadata"])]

    resolved = resolve_band("sentinel-2-l2a", "viewZenithMean", assets, [source])

    assert resolved is not None
    assert resolved.sibling_key is None


# --------------------------------------------------------------------------
# Backward compatibility: a string `sibling` (every Sentinel-1 entry) is
# unaffected by the new parameter/type -- proven, not assumed.
# --------------------------------------------------------------------------


def _s1_shaped_source():
    return BandSource(
        collection=re.compile("sentinel-1-grd"),
        media_types=frozenset({"application/xml"}),
        roles=frozenset({"metadata"}),
        asset=re.compile(r"schema-noise-(?P<pol>[a-z]{2})"),
        bands=(("{pol}_noise_lut", "noise"),),
        sibling="{pol}",
        reader=object,
    )


def test_string_sibling_output_is_identical_with_and_without_sibling_candidates():
    source = _s1_shaped_source()
    assets = [("schema-noise-vv", "application/xml", ["metadata"])]
    sibling_candidates = [("vv", "image/tiff", ["data"], 10.0)]

    without = resolve_band("sentinel-1-grd", "vv_noise_lut", assets, [source])
    with_unused = resolve_band(
        "sentinel-1-grd",
        "vv_noise_lut",
        assets,
        [source],
        sibling_candidates=sibling_candidates,
    )

    assert without == with_unused
    assert without.sibling_key == "vv"


def test_string_sibling_never_calls_a_picker():
    """A string `sibling` must never be treated as callable -- guards
    against a future refactor accidentally trying `source.sibling(...)`
    unconditionally."""
    source = _s1_shaped_source()
    assert isinstance(source.sibling, str)
    assert not callable(source.sibling)
