"""Multi-band assets end to end (titiler/openeo/assetbands.py), against a real
EOPF Sentinel-2 L2A item (tests/fixtures/eopf/items/sentinel2_l2a.json).

`test_assetbands.py` covers the resolver itself in isolation; this file covers
the four call sites it feeds, and -- the property that matters most -- that
discovery and the read path agree on every name.
"""

import datetime
import json
from pathlib import Path

import pystac
import pytest
import rasterio

from titiler.openeo.reader import (
    SimpleSTACReader,
    _get_assets_resolutions,
    _get_cube_resolutions,
)
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

#: Each band's own STAC `name`, keyed by its eo:common_name above -- resolves
#: alongside it (issue #397), read straight from the fixture rather than
#: hand-transcribed so the two stay honest with each other.
_reflectance_bands = json.loads(FIXTURE.read_text())["assets"]["reflectance"]["bands"]
EXPECTED_RAW_NAMES = {b["eo:common_name"]: b["name"] for b in _reflectance_bands}
assert set(EXPECTED_RAW_NAMES) == set(EXPECTED_BANDS)

#: The item's other assets: single-band or band-less, so unaffected by any of
#: this -- kept as their own asset key.
UNCHANGED_ASSET_KEYS = {"AOT_10m", "SCL_20m", "WVP_10m"}


@pytest.fixture
def eopf_item() -> pystac.Item:
    return pystac.Item.from_dict(json.loads(FIXTURE.read_text()))


@pytest.fixture
def eopf_collection(eopf_item: pystac.Item) -> pystac.Collection:
    """A minimal collection whose item_assets mirror the fixture item's own
    assets -- the shape `getdimensions`/`_sync_band_summaries` read."""
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
    """Both a band's display name and its own STAC `name` are advertised
    (issue #397) -- a name the read path accepts but discovery does not
    advertise would be just as inconsistent as the reverse."""
    backend = stacApiBackend.__new__(stacApiBackend)
    dims = backend.getdimensions(eopf_collection)

    spectral = set(dims["spectral"].to_dict()["values"])

    assert (
        spectral
        == set(EXPECTED_BANDS) | set(EXPECTED_RAW_NAMES.values()) | UNCHANGED_ASSET_KEYS
    )
    assert "reflectance" not in spectral, "the asset key must not itself be advertised"


# ---------------------------------------------------------------------------
# Discovery: _sync_band_summaries
# ---------------------------------------------------------------------------


def _summarised(collection: pystac.Collection) -> dict:
    """The collection as `get_collection` returns it."""
    backend = stacApiBackend(url="https://example.com")
    col_dict = backend.add_data_cubes_if_missing(collection).to_dict()
    backend._fix_collection(col_dict)
    return col_dict


def test_sync_band_summaries_expands_reflectance_with_spectral_metadata(
    eopf_collection,
):
    collection = _summarised(eopf_collection)

    summaries = {b["name"]: b for b in collection["summaries"]["bands"]}

    assert set(EXPECTED_BANDS) <= set(summaries)
    blue = summaries["blue"]
    assert blue["eo:common_name"] == "blue"
    assert blue["gsd"] == 10
    assert "eo:center_wavelength" in blue

    coastal = summaries["coastal"]
    assert coastal["gsd"] == 20

    # Its own STAC name gets an entry too (issue #397), with the same
    # metadata as its display name's entry.
    assert set(EXPECTED_RAW_NAMES.values()) <= set(summaries)
    assert summaries["b02"]["eo:common_name"] == "blue"
    assert summaries["b02"]["gsd"] == 10


def test_sync_band_summaries_mirror_the_band_dimension(eopf_collection):
    """Band-less data assets (AOT_10m/SCL_20m/WVP_10m) are bands of the cube,
    so they get a summary entry too (issue #440). The multi-band asset key and
    the thumbnail are not bands of the cube, so they get none."""
    collection = _summarised(eopf_collection)

    values = collection["cube:dimensions"]["spectral"]["values"]
    names = [b["name"] for b in collection["summaries"]["bands"]]
    assert names == values
    assert names == [b["name"] for b in collection["summaries"]["eo:bands"]]
    assert UNCHANGED_ASSET_KEYS <= set(names)
    assert set(names).isdisjoint({"reflectance", "thumbnail"})

    blue = {b["name"]: b for b in collection["summaries"]["bands"]}["blue"]
    assert "multi-band asset 'reflectance'" in blue["description"]


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


def test_get_asset_info_resolves_raw_stac_names_too(eopf_item):
    """The literal repro from issue #397: `load_collection(bands=["b04"])`
    must resolve, the same way `bands=["red"]` already did, since `b04` is
    the band's own declared `name` in the same catalogue metadata."""
    with SimpleSTACReader(eopf_item) as src:
        for common_name, raw_name in EXPECTED_RAW_NAMES.items():
            by_common_name = src._get_asset_info(common_name)
            by_raw_name = src._get_asset_info(raw_name)
            assert by_raw_name["name"] == "reflectance"
            assert (
                by_raw_name["method_options"]["indexes"]
                == by_common_name["method_options"]["indexes"]
            )


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

    with SimpleSTACReader(eopf_item) as src_dst:
        resolutions = _get_cube_resolutions(
            [src_dst], target_crs, target_bbox, list(EXPECTED_BANDS)
        )
    (per_band,) = resolutions.values()

    for name, expected_gsd in EXPECTED_BANDS.items():
        (x_res, y_res, _bbox) = per_band[name][0]
        assert x_res == expected_gsd
        assert y_res == expected_gsd


@pytest.mark.parametrize("selector", [{"bands": ["coastal"]}, {"indexes": [1]}])
def test_asset_dict_selection_uses_the_selected_bands_own_gsd(eopf_item, selector):
    """`{"name": "reflectance", "bands": ["coastal"]}` (what a backend's
    `_bands_to_assets` gives) gets b01's native 20m, the same as requesting
    `coastal` directly -- not the asset's own 10m."""
    with SimpleSTACReader(eopf_item) as src_dst:
        resolutions = _get_assets_resolutions(
            eopf_item,
            src_dst,
            bands=["reflectance|coastal"],
            assets=[{"name": "reflectance", **selector}],
        )

    x_res, y_res, _crs = resolutions["reflectance|coastal"]
    assert (x_res, y_res) == (20, 20)


def test_cube_resolutions_gsd_fallback_covers_assets_with_no_proj_transform(
    eopf_item,
):
    """AOT_10m/SCL_20m/WVP_10m carry only `gsd`, no `proj:transform` or
    `proj:shape` -- before the fallback in `_get_asset_resolution`, these
    silently contributed no resolution at all."""
    target_crs = rasterio.crs.CRS.from_epsg(32627)
    target_bbox = list(eopf_item.bbox)

    with SimpleSTACReader(eopf_item) as src_dst:
        resolutions = _get_cube_resolutions(
            [src_dst], target_crs, target_bbox, ["AOT_10m", "SCL_20m", "WVP_10m"]
        )
    (per_band,) = resolutions.values()

    assert per_band["AOT_10m"][0][:2] == (10.0, 10.0)
    assert per_band["SCL_20m"][0][:2] == (20.0, 20.0)
    assert per_band["WVP_10m"][0][:2] == (10.0, 10.0)


@pytest.fixture
def geographic_eopf_item(eopf_item) -> pystac.Item:
    """The same item re-labelled as EPSG:4326 -- `gsd` is still in metres, but
    the CRS's units are now degrees (issue #381)."""
    eopf_item.properties["proj:code"] = "EPSG:4326"
    eopf_item.properties.pop("proj:bbox", None)
    eopf_item.assets["reflectance"].extra_fields["proj:code"] = "EPSG:4326"
    return eopf_item


def test_gsd_fallback_skipped_for_geographic_crs(geographic_eopf_item):
    """An asset with only `gsd` gets no resolution in a geographic CRS,
    rather than 10 metres read as 10 degrees."""
    with SimpleSTACReader(geographic_eopf_item) as src_dst:
        resolutions = _get_assets_resolutions(
            geographic_eopf_item, src_dst, bands=["AOT_10m", "SCL_20m"]
        )

    assert resolutions == {}


def test_band_gsd_skipped_for_geographic_crs(geographic_eopf_item):
    """A band's own `gsd` is not used in a geographic CRS either; the owning
    asset's `proj:shape` (in CRS units) is used instead."""
    with SimpleSTACReader(geographic_eopf_item) as src_dst:
        resolutions = _get_assets_resolutions(
            geographic_eopf_item, src_dst, bands=["blue", "coastal"]
        )

    bbox = geographic_eopf_item.bbox
    expected = (
        abs(bbox[2] - bbox[0]) / 10980,
        abs(bbox[3] - bbox[1]) / 10980,
    )
    for name in ("blue", "coastal"):
        x_res, y_res, crs = resolutions[name]
        assert crs.is_geographic
        assert (x_res, y_res) == pytest.approx(expected)


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


# ---------------------------------------------------------------------------
# Planetary Computer `visual`: a copy of other assets, qualified (issue #440)
# ---------------------------------------------------------------------------


def _pc_like_item() -> pystac.Item:
    item = pystac.Item(
        id="pc-like",
        geometry={
            "type": "Polygon",
            "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]],
        },
        bbox=[0, 0, 1, 1],
        datetime=datetime.datetime(2026, 8, 7, tzinfo=datetime.timezone.utc),
        properties={},
    )
    visual_bands = [
        {"name": "B04", "common_name": "red"},
        {"name": "B03", "common_name": "green"},
        {"name": "B02", "common_name": "blue"},
    ]
    for key, bands in (
        ("B04", [{"name": "B04", "common_name": "red"}]),
        ("B03", [{"name": "B03", "common_name": "green"}]),
        ("B02", [{"name": "B02", "common_name": "blue"}]),
        ("visual", visual_bands),
    ):
        item.add_asset(
            key,
            pystac.Asset(
                href=f"https://example.com/{key}.tif",
                media_type=pystac.MediaType.COG,
                roles=["data"],
                extra_fields={"eo:bands": bands},
            ),
        )
    return item


def test_pc_red_reads_b04_and_visual_red_reads_visual():
    with SimpleSTACReader(_pc_like_item()) as src:
        visual_red = src._get_asset_info("visual_red")
        assert visual_red["name"] == "visual"
        assert visual_red["method_options"]["indexes"] == [1]
        assert src._get_asset_info("visual_green")["method_options"]["indexes"] == [2]

        # Data before visuals: `red` is the whole B04 asset, same as `B04`.
        for name in ("B04", "red"):
            info = src._get_asset_info(name)
            assert info["name"] == "B04"
            assert "indexes" not in info["method_options"]

        # No second name for the same 8-bit band.
        with pytest.raises(Exception, match="visual_B04"):
            src._get_asset_info("visual_B04")


def test_every_advertised_pc_band_is_readable():
    """Same property as above, for Planetary Computer: common names, the
    qualified `visual` bands and the derived angle bands."""
    data = json.loads(
        Path(
            "tests/fixtures/sentinel2/collections/planetary_computer_full.json"
        ).read_text()
    )
    collection = pystac.Collection.from_dict(data)
    backend = stacApiBackend.__new__(stacApiBackend)
    advertised = set(backend.getdimensions(collection)["spectral"].to_dict()["values"])

    item = pystac.Item(
        id="pc-like",
        geometry={
            "type": "Polygon",
            "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]],
        },
        bbox=[0, 0, 1, 1],
        datetime=datetime.datetime(2026, 8, 7, tzinfo=datetime.timezone.utc),
        properties={},
        collection="sentinel-2-l2a",
    )
    for key, asset in data["item_assets"].items():
        item.add_asset(
            key,
            pystac.Asset(
                href=f"https://example.com/{key}",
                media_type=asset.get("type"),
                roles=asset.get("roles"),
                extra_fields={
                    k: v for k, v in asset.items() if k not in ("type", "roles")
                },
            ),
        )

    assert {"red", "visual_red", "sunZenithAngles"} <= advertised
    with SimpleSTACReader(item) as src:
        for name in advertised:
            src._get_asset_info(name)  # must not raise
