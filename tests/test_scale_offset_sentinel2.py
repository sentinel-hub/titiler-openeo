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
