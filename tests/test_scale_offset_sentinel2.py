"""Tests for the "sentinel2-boa" scale/offset plugin (Sentinel-2 L2A MTD_MSIL2A.xml).

See titiler/openeo/scaleoffset/sentinel2.py and
docs/adr/0009-scale-offset-sources.md. Fixtures: tests/fixtures/sentinel2/README.md.
"""

import copy
import json
from datetime import datetime
from pathlib import Path

import numpy
import pystac
import pytest
from rio_tiler.models import ImageData

import titiler.openeo.reader as reader
from titiler.openeo.scaleoffset import PLUGINS, find_metadata_asset
from titiler.openeo.scaleoffset import sentinel2 as s2

FIXTURES = Path(__file__).parent / "fixtures" / "sentinel2"
PB0513 = (FIXTURES / "mtd_msil2a_pb0513.xml").read_bytes()
PB0300 = (FIXTURES / "mtd_msil2a_pb0300.xml").read_bytes()

#: Each catalogue's own spelling of the product-metadata asset key.
PRODUCT_METADATA_KEY = {
    "cdse": "product_metadata",
    "earth_search": "product_metadata",
    "planetary_computer": "product-metadata",
}
#: Each catalogue's key for the B04 (red) asset.
RED_KEY = {"cdse": "B04_10m", "earth_search": "red", "planetary_computer": "B04"}

PLUGIN = PLUGINS["sentinel2-boa"]


@pytest.fixture(autouse=True)
def _clear_cache():
    s2._product_cache.clear()
    yield
    s2._product_cache.clear()


def _item(catalogue: str, *, with_product_metadata: bool = True) -> pystac.Item:
    data = json.loads((FIXTURES / "items" / f"{catalogue}.json").read_text())
    if with_product_metadata:
        data["assets"][PRODUCT_METADATA_KEY[catalogue]] = {
            "href": f"https://example.com/{catalogue}/MTD_MSIL2A.xml",
            "type": "application/xml",
            "roles": ["metadata"],
        }
    return pystac.Item.from_dict(data)


class _FixtureFetcher:
    def __init__(self, payload=PB0513, error=None):
        self.payload = payload
        self.error = error
        self.calls = []

    def fetch(self, href):
        self.calls.append(href)
        if self.error:
            raise self.error
        return self.payload


# --- parser ---------------------------------------------------------------------
def test_parse_pb0513_has_boa_offset():
    pairs = s2.parse_product_scale_offset(PB0513)
    assert pairs["B4"] == pytest.approx((0.0001, -0.1))
    assert pairs["B8A"] == pytest.approx((0.0001, -0.1))
    assert pairs["B12"] == pytest.approx((0.0001, -0.1))
    assert len([k for k in pairs if k.startswith("B")]) == 13


def test_parse_pb0300_has_no_offset():
    pairs = s2.parse_product_scale_offset(PB0300)
    assert pairs["B4"] == pytest.approx((0.0001, 0.0))


def test_parse_aot_wvp_quantification():
    pairs = s2.parse_product_scale_offset(PB0513)
    assert pairs["AOT"] == pytest.approx((0.001, 0.0))
    assert pairs["WVP"] == pytest.approx((0.001, 0.0))
    assert "SCL" not in pairs


def test_parse_rejects_file_without_quantification():
    with pytest.raises(ValueError, match="BOA_QUANTIFICATION_VALUE"):
        s2.parse_product_scale_offset(b"<Level-2A_User_Product/>")


# --- band key -------------------------------------------------------------------
@pytest.mark.parametrize("catalogue", ["cdse", "earth_search", "planetary_computer"])
def test_band_key_same_on_all_catalogues(catalogue):
    item = _item(catalogue)
    fields = item.assets[RED_KEY[catalogue]].extra_fields
    assert s2.band_key(fields) == "B4"


@pytest.mark.parametrize(
    "name, key",
    [("B04", "B4"), ("B4", "B4"), ("B8A", "B8A"), ("b8a", "B8A"), ("B12", "B12")]
    + [("AOT", "AOT"), ("WVP", "WVP"), ("SCL", None), ("red", None)],
)
def test_band_key_names(name, key):
    assert s2.band_key({"eo:bands": [{"name": name}]}) == key
    assert s2.band_key({"bands": [{"name": name}]}) == key


def test_band_key_no_bands():
    assert s2.band_key({}) is None


# --- asset discovery --------------------------------------------------------------
@pytest.mark.parametrize("catalogue", ["cdse", "earth_search", "planetary_computer"])
def test_find_product_metadata_on_all_catalogues(catalogue):
    asset = find_metadata_asset(PLUGIN, _item(catalogue))
    assert asset is not None
    assert asset.href.endswith(f"{catalogue}/MTD_MSIL2A.xml")


def test_granule_metadata_is_not_product_metadata():
    # Earth Search items before 2022 carry granule_metadata only.
    item = _item("earth_search", with_product_metadata=False)
    assert "granule_metadata" in item.assets
    assert find_metadata_asset(PLUGIN, item) is None


def test_other_collection_does_not_apply():
    item = _item("planetary_computer")
    item.collection_id = "landsat-c2-l2"
    assert find_metadata_asset(PLUGIN, item) is None


# --- through the reader ------------------------------------------------------------
class _Src:
    """The parts of SimpleSTACReader that `_plugin_pairs_for` uses."""

    def __init__(self, item, fetcher, signer=None):
        self.input = item
        self.band_source_fetcher = fetcher
        self.signer = signer


def test_plugin_pairs_signs_fetch_and_caches_on_unsigned_href():
    item = _item("planetary_computer")
    fetcher = _FixtureFetcher()
    load = reader._plugin_pairs_for(_Src(item, fetcher, signer=lambda h: h + "?sas=1"))
    assert load("sentinel2-boa")["B4"] == pytest.approx((0.0001, -0.1))
    assert fetcher.calls == [
        "https://example.com/planetary_computer/MTD_MSIL2A.xml?sas=1"
    ]

    # A new token: same unsigned href, so the cache serves it -- no new fetch.
    load = reader._plugin_pairs_for(_Src(item, fetcher, signer=lambda h: h + "?sas=2"))
    load("sentinel2-boa")
    assert len(fetcher.calls) == 1


def test_plugin_pairs_not_applicable_does_not_fetch():
    fetcher = _FixtureFetcher()
    item = _item("earth_search", with_product_metadata=False)
    assert reader._plugin_pairs_for(_Src(item, fetcher))("sentinel2-boa") is None
    assert fetcher.calls == []


def test_plugin_pairs_fetch_error_raises():
    fetcher = _FixtureFetcher(error=OSError("403"))
    load = reader._plugin_pairs_for(_Src(_item("planetary_computer"), fetcher))
    with pytest.raises(OSError):
        load("sentinel2-boa")


def _read(monkeypatch, item, dn, fetcher, sources):
    """Run `reader._reader` with a stand-in SimpleSTACReader and set sources."""
    img = ImageData(
        numpy.ma.MaskedArray(numpy.asarray(dn, dtype="uint16")),
        band_names=[f"b{i + 1}" for i in range(len(dn))],
    )

    class _FakeReader(_Src):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def part(self, bbox, **kwargs):
            return copy.deepcopy(img)

    monkeypatch.setattr(
        reader, "SimpleSTACReader", lambda item, **kw: _FakeReader(item, fetcher)
    )
    settings = reader.processing_settings
    monkeypatch.setattr(settings, "apply_scale_offset", True)
    monkeypatch.setattr(settings, "scale_offset_sources", sources)
    monkeypatch.setattr(settings, "scale_offset_collections", {})
    return reader._reader(item, (0, 0, 1, 1), assets=["B04", "SCL"])


def _mpc_item():
    item = _item("planetary_computer")
    item.add_asset(
        "SCL",
        pystac.Asset(
            href="https://example.com/SCL.tif",
            extra_fields={"eo:bands": [{"name": "SCL"}]},
        ),
    )
    return item


@pytest.mark.parametrize(
    "payload, expected", [(PB0513, 0.1), (PB0300, 0.2)]
)  # (2000 - 1000) / 10000 and 2000 / 10000
def test_reader_mpc_like_item_gives_reflectance(monkeypatch, payload, expected):
    out = _read(
        monkeypatch,
        _mpc_item(),
        [[[2000]], [[4]]],
        _FixtureFetcher(payload),
        ["stac", "sentinel2-boa", "cog"],
    )
    assert out.array.dtype == numpy.float32
    assert out.array.data[0][0, 0] == pytest.approx(expected, rel=1e-5)
    assert out.array.data[1][0, 0] == 4.0  # SCL unchanged


def test_reader_stac_first_never_fetches_when_stac_covers(monkeypatch):
    item = _mpc_item()
    item.assets["B04"].extra_fields["raster:scale"] = 0.0001
    item.assets["B04"].extra_fields["raster:offset"] = -0.1
    fetcher = _FixtureFetcher()
    # SCL has no STAC pair, so the walk reaches the plugin for SCL too. The
    # plugin has no key for SCL, so its file must still not be fetched.
    out = _read(
        monkeypatch, item, [[[2000]], [[4]]], fetcher, ["stac", "sentinel2-boa", "cog"]
    )
    assert out.array.data[0][0, 0] == pytest.approx(0.1, rel=1e-5)
    assert out.array.data[1][0, 0] == 4.0
    assert fetcher.calls == []


def test_reader_lazy_stack_does_not_fetch(monkeypatch):
    from titiler.openeo.processes.implementations.data_model import RasterStack

    fetcher = _FixtureFetcher()
    item = _mpc_item()

    def task():
        return _read(monkeypatch, item, [[[2000]], [[4]]], fetcher, ["sentinel2-boa"])

    dt = datetime(2026, 9, 28)
    stack = RasterStack(
        tasks=[(task, {"id": "x", "datetime": dt})],
        timestamp_fn=lambda asset: asset["datetime"],
    )
    assert list(stack.keys()) == [dt]
    assert fetcher.calls == []
    assert stack[dt].array.data[0][0, 0] == pytest.approx(0.1, rel=1e-5)
    assert len(fetcher.calls) == 1


# --- NODATA from Special_Values (#463) ----------------------------------------------
@pytest.mark.parametrize("payload", [PB0513, PB0300])
def test_parse_nodata(payload):
    assert s2.parse_product_nodata(payload) == 0.0


def test_parse_nodata_absent():
    assert s2.parse_product_nodata(b"<Level-2A_User_Product/>") is None


def test_nodata_and_pairs_share_one_fetch():
    fetcher = _FixtureFetcher()
    href = "https://example.com/MTD_MSIL2A.xml"
    assert s2.get_product_nodata(href, fetcher=fetcher) == 0.0
    assert s2.get_product_scale_offset(href, fetcher=fetcher)["B4"] == pytest.approx(
        (0.0001, -0.1)
    )
    assert len(fetcher.calls) == 1


def _set_sources(monkeypatch, sources):
    settings = reader.processing_settings
    monkeypatch.setattr(settings, "apply_scale_offset", True)
    monkeypatch.setattr(settings, "scale_offset_sources", sources)
    monkeypatch.setattr(settings, "scale_offset_collections", {})


def _asset_nodata(item, asset, fetcher):
    """``(method nodata, plugin fallback value or None)`` for ``asset``."""
    with reader.SimpleSTACReader(item, band_source_fetcher=fetcher) as src:
        info = src._get_asset_info(asset)
    fallback = info["reader_options"].get("fallback_nodata")
    return info["method_options"].get("nodata"), fallback and fallback()


def test_asset_info_plugin_nodata_is_lazy(monkeypatch):
    _set_sources(monkeypatch, ["stac", "sentinel2-boa", "cog"])
    fetcher = _FixtureFetcher()
    with reader.SimpleSTACReader(_mpc_item(), band_source_fetcher=fetcher) as src:
        info = src._get_asset_info("B04")
    assert "nodata" not in info["method_options"]
    assert fetcher.calls == []  # nothing is fetched before the raster is open
    assert info["reader_options"]["fallback_nodata"]() == 0.0
    assert len(fetcher.calls) == 1


def test_asset_info_plugin_nodata_skips_unknown_band(monkeypatch):
    # The plugin has no key for SCL: no fallback, and no fetch.
    _set_sources(monkeypatch, ["sentinel2-boa"])
    fetcher = _FixtureFetcher()
    assert _asset_nodata(_mpc_item(), "SCL", fetcher) == (None, None)
    assert fetcher.calls == []


@pytest.mark.parametrize(
    "fields",
    [
        {"nodata": 7},
        {"raster:bands": [{"nodata": 0}]},  # 0 must not be lost
        {"raster:bands": [{"raster:nodata": 0}]},
    ],
)
def test_asset_info_stac_nodata_wins(monkeypatch, fields):
    _set_sources(monkeypatch, ["sentinel2-boa"])
    item = _mpc_item()
    item.assets["B04"].extra_fields.update(fields)
    fetcher = _FixtureFetcher()
    expected = fields.get("nodata", 0)
    assert _asset_nodata(item, "B04", fetcher) == (expected, None)
    assert fetcher.calls == []


@pytest.mark.parametrize("sources", [["stac", "cog"], []])
def test_asset_info_plugin_not_listed(monkeypatch, sources):
    _set_sources(monkeypatch, sources)
    fetcher = _FixtureFetcher()
    assert _asset_nodata(_mpc_item(), "B04", fetcher) == (None, None)
    assert fetcher.calls == []


def _read_swath_border(monkeypatch, tmp_path, header_nodata=None):
    """Read an 8x8 B04 GeoTIFF whose 3 left columns are DN 0 (#463).

    The 2 right columns hold ``header_nodata`` when it is given, and the
    header then declares it. Returns the read and the plugin fetcher.
    """
    import rasterio
    from rasterio.crs import CRS
    from rasterio.transform import from_origin

    dn = numpy.full((8, 8), 2000, dtype="uint16")
    dn[:, :3] = 0  # the swath border
    if header_nodata is not None:
        dn[:, 6:] = header_nodata
    path = tmp_path / "B04.tif"
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=8,
        height=8,
        count=1,
        dtype="uint16",
        crs="EPSG:32631",
        transform=from_origin(600000, 5000080, 10, 10),
        nodata=header_nodata,
    ) as dst:
        dst.write(dn, 1)

    item = _mpc_item()
    b04 = item.assets["B04"]
    b04.href = str(path)
    for key in ("proj:bbox", "proj:shape", "proj:transform"):
        b04.extra_fields.pop(key, None)
    _set_sources(monkeypatch, ["stac", "sentinel2-boa", "cog"])
    fetcher = _FixtureFetcher()
    monkeypatch.setattr(s2, "get_default_fetcher", lambda: fetcher)

    out = reader._reader(
        item,
        (600000, 5000000, 600080, 5000080),
        assets=["B04"],
        bounds_crs=CRS.from_epsg(32631),
        dst_crs=CRS.from_epsg(32631),
        max_size=None,
        height=8,
        width=8,
    )
    return out, fetcher


def test_reader_masks_swath_border_before_offset(monkeypatch, tmp_path):
    """DN 0 outside the swath is masked, not returned as -0.1 (#463)."""
    out, fetcher = _read_swath_border(monkeypatch, tmp_path)
    mask = numpy.ma.getmaskarray(out.array)[0]
    assert mask[:, :3].all()
    assert not mask[:, 3:].any()
    assert out.array.data[0][0, 5] == pytest.approx(0.1, rel=1e-5)
    assert len(fetcher.calls) == 1


def test_reader_header_nodata_wins_over_plugin(monkeypatch, tmp_path):
    """A header nodata is kept, and the product XML is not fetched for it."""
    out, fetcher = _read_swath_border(monkeypatch, tmp_path, header_nodata=65535)
    mask = numpy.ma.getmaskarray(out.array)[0]
    assert mask[:, 6:].all()  # the header's nodata
    assert not mask[:, :6].any()  # DN 0 is not the nodata of this raster
    # The plugin XML is still fetched one time, for the scale/offset only.
    assert len(fetcher.calls) == 1
