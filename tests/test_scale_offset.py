"""Tests for STAC raster:scale/raster:offset application in the reader.

CDSE sentinel-2-l2a bands declare raster:scale=0.0001 / raster:offset=-0.1 (BOA
offset, processing baseline >= 04.00). The reader applies them per band so bands
are returned as physical reflectance (float32) instead of raw DN, while bands
without scale/offset (e.g. SCL) are left untouched. See reader._apply_scale_offset.
"""

from datetime import datetime

import numpy
import pytest
from rio_tiler.models import ImageData

import titiler.openeo.reader as reader
from titiler.openeo.reader import (
    _apply_scale_offset,
    _asset_extra_fields,
    _band_scale_offset,
)
from titiler.openeo.settings import ProcessingSettings


# --- _band_scale_offset -----------------------------------------------------
def test_band_scale_offset_asset_level():
    assert _band_scale_offset({"raster:scale": 0.0001, "raster:offset": -0.1}) == (
        0.0001,
        -0.1,
    )


def test_band_scale_offset_raster_bands_fallback():
    assert _band_scale_offset(
        {"raster:bands": [{"scale": 0.0001, "offset": -0.1}]}
    ) == (
        0.0001,
        -0.1,
    )
    # newer "bands" with raster: prefixed keys
    assert _band_scale_offset(
        {"bands": [{"raster:scale": 2.0, "raster:offset": 1.0}]}
    ) == (2.0, 1.0)


def test_band_scale_offset_default_identity():
    assert _band_scale_offset({}) == (1.0, 0.0)


# --- _asset_extra_fields ----------------------------------------------------
def test_asset_extra_fields_from_dict():
    item = {"assets": {"B02_10m": {"raster:scale": 0.0001}}}
    assert _asset_extra_fields(item, "B02_10m") == {"raster:scale": 0.0001}
    assert _asset_extra_fields(item, "MISSING") == {}


def test_asset_extra_fields_from_pystac_item():
    import pystac

    item = pystac.Item(
        id="x",
        geometry={"type": "Point", "coordinates": [0, 0]},
        bbox=[0, 0, 0, 0],
        datetime=datetime(2024, 6, 14),
        properties={},
    )
    item.add_asset(
        "B02_10m",
        pystac.Asset(href="x.jp2", extra_fields={"raster:scale": 0.0001}),
    )
    assert _asset_extra_fields(item, "B02_10m")["raster:scale"] == 0.0001
    assert _asset_extra_fields(item, "MISSING") == {}


# --- _apply_scale_offset ----------------------------------------------------
def _img(dn, mask=False, dtype="uint16"):
    arr = numpy.ma.MaskedArray(numpy.asarray(dn, dtype=dtype), mask=mask)
    return ImageData(arr, band_names=[f"b{i + 1}" for i in range(arr.shape[0])])


def test_apply_scale_offset_per_band_float32_mask_preserved():
    # band 0 = B02 (scale/offset), band 1 = SCL (none)
    img = _img(
        [[[2000, 1500]], [[4, 9]]],
        mask=[[[False, True]], [[False, True]]],
    )
    item = {
        "assets": {
            "B02_10m": {"raster:scale": 0.0001, "raster:offset": -0.1},
            "SCL_20m": {},
        }
    }
    out = _apply_scale_offset(img, item, ["B02_10m", "SCL_20m"])

    # float32, NOT float64
    assert out.array.dtype == numpy.float32
    # B02: DN*0.0001 - 0.1
    numpy.testing.assert_allclose(out.array.data[0], [[0.1, 0.05]], rtol=1e-5)
    # SCL untouched (identity scale/offset)
    numpy.testing.assert_array_equal(out.array.data[1], [[4.0, 9.0]])
    # mask preserved per band
    assert numpy.ma.getmaskarray(out.array).tolist() == [
        [[False, True]],
        [[False, True]],
    ]


def test_apply_scale_offset_noop_when_all_identity_keeps_dtype():
    img = _img([[[4, 9]]])  # SCL only, no scale/offset
    out = _apply_scale_offset(img, {"assets": {"SCL_20m": {}}}, ["SCL_20m"])
    assert out is img  # unchanged object
    assert out.array.dtype == numpy.dtype("uint16")  # integer dtype preserved


def test_apply_scale_offset_noop_on_band_asset_mismatch():
    img = _img([[[2000, 1500]], [[4, 9]]])  # 2 bands
    out = _apply_scale_offset(img, {"assets": {}}, ["only_one_asset"])
    assert out is img


def test_apply_scale_offset_noop_when_assets_missing():
    img = _img([[[2000, 1500]]])
    assert _apply_scale_offset(img, {"assets": {}}, None) is img


# --- _reader integration (flag on/off), still lazy --------------------------
class _FakeSrc:
    """Stand-in for SimpleSTACReader as a context manager returning a fixed part."""

    def __init__(self, img):
        self._img = img
        self.calls = 0

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def part(self, bbox, **kwargs):
        self.calls += 1
        return self._img


@pytest.fixture
def s2_item():
    return {
        "id": "s2",
        "properties": {"datetime": "2024-06-14T10:00:00Z"},
        "assets": {
            "B02_10m": {"raster:scale": 0.0001, "raster:offset": -0.1},
            "SCL_20m": {},
        },
    }


def test_reader_applies_scale_offset(monkeypatch, s2_item):
    img = _img([[[2000, 0]], [[4, 0]]], mask=[[[False, True]], [[False, True]]])
    fake = _FakeSrc(img)
    monkeypatch.setattr(reader, "SimpleSTACReader", lambda item: fake)
    monkeypatch.setattr(reader.processing_settings, "apply_scale_offset", True)

    out = reader._reader(s2_item, (0, 0, 1, 1), assets=["B02_10m", "SCL_20m"])
    assert out.array.dtype == numpy.float32
    assert out.array.data[0][0, 0] == pytest.approx(0.1, rel=1e-5)  # B02 reflectance
    assert out.array.data[1][0, 0] == 4.0  # SCL untouched


def test_reader_flag_off_returns_raw_dn(monkeypatch, s2_item):
    img = _img([[[2000, 0]], [[4, 0]]])
    monkeypatch.setattr(reader, "SimpleSTACReader", lambda item: _FakeSrc(img))
    monkeypatch.setattr(reader.processing_settings, "apply_scale_offset", False)

    out = reader._reader(s2_item, (0, 0, 1, 1), assets=["B02_10m", "SCL_20m"])
    assert out.array.dtype == numpy.dtype("uint16")
    assert out.array.data[0][0, 0] == 2000  # raw DN, not scaled


# --- laziness ---------------------------------------------------------------
def test_scale_offset_is_lazy(monkeypatch, s2_item):
    """Building a RasterStack must not read/scale anything; the scale/offset only
    runs inside the task, on first access to a slice."""
    from titiler.openeo.processes.implementations.data_model import RasterStack

    img = _img([[[2000, 0]], [[4, 0]]], mask=[[[False, True]], [[False, True]]])
    calls = {"n": 0}

    def task():
        calls["n"] += 1
        monkeypatch.setattr(reader, "SimpleSTACReader", lambda item: _FakeSrc(img))
        return reader._reader(s2_item, (0, 0, 1, 1), assets=["B02_10m", "SCL_20m"])

    dt = datetime(2024, 6, 14)
    stack = RasterStack(
        tasks=[(task, {"id": "s2", "datetime": dt})],
        timestamp_fn=lambda asset: asset["datetime"],
    )

    # Building + listing keys must not execute the task.
    assert calls["n"] == 0
    assert list(stack.keys()) == [dt]
    assert calls["n"] == 0

    # First access executes the task and applies scale/offset (float32 reflectance).
    out = stack[dt]
    assert calls["n"] == 1
    assert out.array.dtype == numpy.float32
    assert out.array.data[0][0, 0] == pytest.approx(0.1, rel=1e-5)


# --- settings ---------------------------------------------------------------
def test_setting_default_on():
    assert ProcessingSettings().apply_scale_offset is True


def test_setting_env_override(monkeypatch):
    monkeypatch.setenv("TITILER_OPENEO_PROCESSING_APPLY_SCALE_OFFSET", "false")
    assert ProcessingSettings().apply_scale_offset is False
