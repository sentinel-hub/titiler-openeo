"""Tests for band-source readers (issue #348, ADR 0002, increment 2).

Increment 1 (tests/test_band_sources_discovery.py) covered discovery only.
This covers production: `NoiseBandReader` end to end through
`SimpleSTACReader`'s `_get_asset_info`/`_get_reader` hooks, mask inheritance,
band ordering, multi-item mosaicking, resolution/pixel-limit accounting for a
derived-only request, and the clean failure for a band discovery advertises
but has no reader for yet (a synthetic case as of increment 3, which gives
`CalibrationBandReader` a reader for the last such band --
see tests/test_calibration_band_reader.py).

The measurement GCP fixture and fetcher double mirror
tests/test_sar_process.py exactly (same GCP grid, same real
`noise_ipf290.xml`), since `NoiseBandReader.part()` only ever opens the
measurement asset header-only for GCPs (`geocode.get_gcps`) -- it never reads
its pixels, so the tiny, deliberately degenerate-at-full-resolution GCP tiff
that file uses is exactly as valid here.
"""

import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pystac
import pytest
import rasterio
from rasterio.control import GroundControlPoint
from rasterio.crs import CRS
from rio_tiler.errors import InvalidAssetName
from rio_tiler.models import ImageData

from titiler.openeo.bandsources.readers import NoiseBandReader
from titiler.openeo.bandsources.registry import ResolvedBand
from titiler.openeo.errors import OutputLimitExceeded
from titiler.openeo.reader import (
    SimpleSTACReader,
    _estimate_output_dimensions,
    _get_assets_resolutions,
    _inherit_derived_band_masks,
)
from titiler.openeo.sar import annotation, geocode

FIXTURES = Path(__file__).parent / "fixtures" / "sar"
_NOISE_XML = (FIXTURES / "noise_ipf290.xml").read_bytes()
_NOISE_HREF = "fixture://band-source-readers-noise-vv"


class _FixtureFetcher:
    """A fake AssetFetcher serving fixed bytes by href, with a call log."""

    def __init__(self, mapping: Dict[str, bytes]):
        self._mapping = mapping
        self.calls: List[str] = []

    def fetch(self, href: str) -> bytes:
        """Return the fixed payload for href, recording the call."""
        self.calls.append(href)
        return self._mapping[href]


def _write_measurement_gcp_tiff(path: Path) -> None:
    """A minimal GCP-referenced GeoTIFF, mirroring test_sar_process.py's
    corner mapping, but as a 4x4 grid rather than 4 corner points.

    `OpenEOReader` fits an order-3 polynomial (`MAX_GCP_ORDER=3`), which
    needs at least 10 points ((3+1)(3+2)/2 coefficients per axis); 4 points
    are not enough on every GDAL build, and "not enough points" is a hard
    GDAL error rather than a graceful fallback on some of them -- confirmed:
    4 corners alone raised `CPLE_AppDefinedError: Failed to compute GCP
    transform: Not enough points available` on Python 3.11/3.12 CI (GitHub
    Actions), while the GDAL build behind Python 3.13 tolerated it (returning
    an all-masked, all-zero result instead of raising). 16 points is
    comfortably overdetermined on every GDAL version tested.

    The GCPs are chosen so a (0,0,1,1)-bounds destination grid lands within
    noise_ipf290.xml's real coordinate domain (line 0-~16000, pixel
    0-~23000). `get_gcps` only reads the header/tags, never pixel data, so
    the file's actual size/content is otherwise irrelevant to the oracle
    tests -- but the *pixel* GCP-warp a real "vv" read goes through needs a
    well-determined fit to not raise at all, whatever it warps to.
    """
    rows = np.linspace(0, 8000, 4)
    cols = np.linspace(0, 12000, 4)
    gcps = [
        GroundControlPoint(row=row, col=col, x=col / 12000, y=1 - row / 8000)
        for row in rows
        for col in cols
    ]
    with rasterio.open(
        path, "w", driver="GTiff", width=2, height=2, count=1, dtype="uint16"
    ) as dst:
        dst.write(np.full((2, 2), 100, dtype="uint16"), 1)
        dst.gcps = (gcps, CRS.from_epsg(4326))


def _s1_item(
    measurement_href: str,
    *,
    noise_href: Optional[str] = _NOISE_HREF,
    calibration_href: Optional[str] = None,
    extra_assets: Optional[Dict[str, pystac.Asset]] = None,
    item_id: str = "s1test",
) -> pystac.Item:
    """A minimal Sentinel-1 GRD item: one polarisation (vv)."""
    item = pystac.Item(
        id=item_id,
        geometry={
            "type": "Polygon",
            "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]],
        },
        bbox=[0, 0, 1, 1],
        datetime=datetime(2024, 1, 1),
        properties={"sar:instrument_mode": "IW"},
        collection="sentinel-1-grd",
    )
    item.add_asset(
        "vv",
        pystac.Asset(
            href=measurement_href,
            media_type="image/tiff; application=geotiff; profile=cloud-optimized",
            roles=["data"],
        ),
    )
    if noise_href:
        item.add_asset(
            "schema-noise-vv",
            pystac.Asset(
                href=noise_href, media_type="application/xml", roles=["metadata"]
            ),
        )
    if calibration_href:
        item.add_asset(
            "schema-calibration-vv",
            pystac.Asset(
                href=calibration_href,
                media_type="application/xml",
                roles=["metadata"],
            ),
        )
    for name, asset in (extra_assets or {}).items():
        item.add_asset(name, asset)
    return item


# --------------------------------------------------------------------------- the oracle


def test_noise_lut_band_matches_the_oracle(tmp_path):
    """`vv_noise_lut` must equal `annotation.get_noise(...).evaluate(...)` at
    the same inverse-mapped coordinates `sar_backscatter` computes today, on
    the same grid -- the exact reference this increment was chosen to have.
    """
    measurement_path = tmp_path / "measurement.tif"
    _write_measurement_gcp_tiff(measurement_path)
    item = _s1_item(str(measurement_path))
    fetcher = _FixtureFetcher({_NOISE_HREF: _NOISE_XML})

    with SimpleSTACReader(item, band_source_fetcher=fetcher) as src_dst:
        img = src_dst.part(
            (0.0, 0.0, 1.0, 1.0),
            assets=["vv_noise_lut"],
            dst_crs=CRS.from_epsg(4326),
            bounds_crs=CRS.from_epsg(4326),
            width=4,
            height=4,
        )

    gcps, gcp_crs = geocode.get_gcps(str(measurement_path))
    inverse = geocode.build_inverse_map(
        gcps, gcp_crs, 4, 4, (0.0, 0.0, 1.0, 1.0), CRS.from_epsg(4326)
    )
    oracle = annotation.get_noise(_NOISE_HREF, fetcher=fetcher).evaluate(
        inverse.line, inverse.pixel
    )

    np.testing.assert_allclose(img.array.data[0], oracle, rtol=1e-6)
    # annotation.get_noise is cached by href -- the oracle's own call above
    # must be a cache hit, not a second fetch.
    assert fetcher.calls == [_NOISE_HREF]


def test_constructing_the_reader_triggers_no_fetch(tmp_path):
    """Resolving derived bands at __attrs_post_init__ time is pure/no-I/O --
    constructing SimpleSTACReader must not itself call the fetcher."""
    measurement_path = tmp_path / "measurement.tif"
    _write_measurement_gcp_tiff(measurement_path)
    item = _s1_item(str(measurement_path))
    fetcher = _FixtureFetcher({_NOISE_HREF: _NOISE_XML})

    with SimpleSTACReader(item, band_source_fetcher=fetcher) as src_dst:
        assert "vv_noise_lut" in src_dst._derived_bands
        assert fetcher.calls == []


# --------------------------------------------------------------------------- wiring


def test_get_reader_dispatches_derived_and_real_bands_differently(tmp_path):
    measurement_path = tmp_path / "measurement.tif"
    _write_measurement_gcp_tiff(measurement_path)
    item = _s1_item(str(measurement_path))

    with SimpleSTACReader(item) as src_dst:
        derived_info = src_dst._get_asset_info("vv_noise_lut")
        real_info = src_dst._get_asset_info("vv")

        assert src_dst._get_reader(derived_info) is NoiseBandReader
        assert src_dst._get_reader(real_info) is src_dst.reader


def test_derived_asset_info_resolves_annotation_and_sibling_hrefs(tmp_path):
    measurement_path = tmp_path / "measurement.tif"
    _write_measurement_gcp_tiff(measurement_path)
    item = _s1_item(str(measurement_path))

    with SimpleSTACReader(item) as src_dst:
        info = src_dst._get_asset_info("vv_noise_lut")

    assert info["url"] == _NOISE_HREF
    assert info["reader_options"]["sibling_href"] == str(measurement_path)


def test_missing_sibling_asset_raises_a_clear_error(tmp_path):
    """schema-noise-vv present but the item is missing the vv measurement
    asset itself -- must fail with a message naming both, not a raw KeyError."""
    item = pystac.Item(
        id="s1-missing-sibling",
        geometry={
            "type": "Polygon",
            "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]],
        },
        bbox=[0, 0, 1, 1],
        datetime=datetime(2024, 1, 1),
        properties={"sar:instrument_mode": "IW"},
        collection="sentinel-1-grd",
    )
    item.add_asset(
        "schema-noise-vv",
        pystac.Asset(
            href=_NOISE_HREF, media_type="application/xml", roles=["metadata"]
        ),
    )

    with SimpleSTACReader(item) as src_dst:
        with pytest.raises(InvalidAssetName, match="sibling asset 'vv'"):
            src_dst._get_asset_info("vv_noise_lut")


def test_band_with_no_reader_yet_fails_clearly(tmp_path, monkeypatch):
    """A band `derive_bands` advertises but whose `BandSource.reader` is
    still `None` -- discovery ahead of a reader existing, increment 1's
    shipped state for every band before its reader landed, and possibly a
    future band again -- must raise InvalidAssetName when requested, not
    silently produce nothing or crash obscurely. Every band this registry
    describes has a reader as of increment 3, so this is exercised against a
    synthetic entry rather than a real (now fully wired) one."""
    from titiler.openeo.bandsources.registry import BandSource

    fake_source = BandSource(
        collection=re.compile("sentinel-1-grd"),
        media_types=frozenset({"application/xml"}),
        roles=frozenset({"metadata"}),
        asset=re.compile(r"schema-fake-(?P<pol>[a-z]{2})"),
        bands=(("{pol}_fake_lut", "fake"),),
        sibling="{pol}",
        reader=None,
    )
    monkeypatch.setattr("titiler.openeo.reader.BAND_SOURCES", [fake_source])

    measurement_path = tmp_path / "measurement.tif"
    _write_measurement_gcp_tiff(measurement_path)
    item = _s1_item(
        str(measurement_path),
        noise_href=None,
        extra_assets={
            "schema-fake-vv": pystac.Asset(
                href="fixture://band-source-fake-vv",
                media_type="application/xml",
                roles=["metadata"],
            )
        },
    )

    with SimpleSTACReader(item) as src_dst:
        assert "vv_fake_lut" not in src_dst._derived_bands
        with pytest.raises(InvalidAssetName):
            src_dst._get_asset_info("vv_fake_lut")


# --------------------------------------------------------------------------- band ordering


@pytest.mark.parametrize("requested", [["vv", "vv_noise_lut"], ["vv_noise_lut", "vv"]])
def test_requested_band_order_is_preserved_with_derived_interleaved(
    tmp_path, requested
):
    measurement_path = tmp_path / "measurement.tif"
    _write_measurement_gcp_tiff(measurement_path)
    item = _s1_item(str(measurement_path))
    fetcher = _FixtureFetcher({_NOISE_HREF: _NOISE_XML})

    with SimpleSTACReader(item, band_source_fetcher=fetcher) as src_dst:
        img = src_dst.part(
            (0.0, 0.0, 1.0, 1.0),
            assets=requested,
            dst_crs=CRS.from_epsg(4326),
            bounds_crs=CRS.from_epsg(4326),
            width=2,
            height=2,
        )

    assert img.array.shape[0] == 2
    assert img.band_descriptions == [f"{name}_b1" for name in requested]


# --------------------------------------------------------------------------- mask inheritance


def _image(mask: np.ndarray, band_names: List[str]) -> ImageData:
    data = np.arange(mask.size, dtype="float32").reshape(mask.shape)
    array = np.ma.MaskedArray(data, mask=mask)
    return ImageData(
        array,
        crs=CRS.from_epsg(4326),
        bounds=(0.0, 0.0, 1.0, 1.0),
        band_names=band_names,
        band_descriptions=band_names,
    )


def test_inherit_derived_band_masks_forces_sibling_mask():
    checkerboard = np.array([[False, True], [True, False]])
    img = _image(
        np.stack([checkerboard, np.zeros((2, 2), dtype=bool)]),
        ["vv", "vv_noise_lut"],
    )
    derived = {
        "vv_noise_lut": ResolvedBand(
            asset_key="schema-noise-vv",
            sibling_key="vv",
            quantity="noise",
            reader=NoiseBandReader,
        )
    }
    original_values = img.array.data.copy()

    out = _inherit_derived_band_masks(img, derived, ["vv", "vv_noise_lut"])

    np.testing.assert_array_equal(out.array.mask[1], checkerboard)
    np.testing.assert_array_equal(out.array.mask[0], out.array.mask[1])
    # Values are untouched -- only the mask changes.
    np.testing.assert_array_equal(out.array.data, original_values)


def test_inherit_derived_band_masks_leaves_derived_only_request_untouched():
    """No sibling requested alongside it -- nothing to inherit from, so the
    derived band's own (fully valid) mask is left as computed."""
    img = _image(np.zeros((1, 2, 2), dtype=bool), ["vv_noise_lut"])
    derived = {
        "vv_noise_lut": ResolvedBand(
            asset_key="schema-noise-vv",
            sibling_key="vv",
            quantity="noise",
            reader=NoiseBandReader,
        )
    }

    out = _inherit_derived_band_masks(img, derived, ["vv_noise_lut"])

    assert not out.array.mask.any()


def test_inherit_derived_band_masks_noop_without_derived_bands():
    img = _image(np.zeros((1, 2, 2), dtype=bool), ["vv"])
    out = _inherit_derived_band_masks(img, {}, ["vv"])
    assert out is img


def test_end_to_end_mask_inheritance_through_simple_stac_reader(tmp_path):
    """Through the real read path: before this fix, the measurement band's
    (fully masked, per the GCP-warp coverage at this scale) mask disagreed
    with the derived band's (honestly unmasked) one -- exactly the trap ADR
    0002 S2.4 describes. After it, they must agree.
    """
    measurement_path = tmp_path / "measurement.tif"
    _write_measurement_gcp_tiff(measurement_path)
    item = _s1_item(str(measurement_path))
    fetcher = _FixtureFetcher({_NOISE_HREF: _NOISE_XML})

    with SimpleSTACReader(item, band_source_fetcher=fetcher) as src_dst:
        img = src_dst.part(
            (0.0, 0.0, 1.0, 1.0),
            assets=["vv", "vv_noise_lut"],
            dst_crs=CRS.from_epsg(4326),
            bounds_crs=CRS.from_epsg(4326),
            width=2,
            height=2,
        )
        img = _inherit_derived_band_masks(
            img, src_dst._derived_bands, ["vv", "vv_noise_lut"]
        )

    np.testing.assert_array_equal(img.array.mask[0], img.array.mask[1])


# --------------------------------------------------------------------------- resolution / pixel-limit accounting


def _item_with_proj_only(width: int = 100, height: int = 50) -> pystac.Item:
    """An item (no GCPs, no rasterio I/O needed) whose vv asset carries
    proj:shape/proj:transform, so resolution comes from STAC metadata alone."""
    item = pystac.Item(
        id="s1-proj-only",
        geometry={
            "type": "Polygon",
            "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]],
        },
        bbox=[0, 0, 1, 1],
        datetime=datetime(2024, 1, 1),
        properties={"sar:instrument_mode": "IW"},
        collection="sentinel-1-grd",
        stac_extensions=[
            "https://stac-extensions.github.io/projection/v1.1.0/schema.json"
        ],
    )
    item.add_asset(
        "vv",
        pystac.Asset(
            href="https://example.com/vv.tif",
            media_type="image/tiff; application=geotiff; profile=cloud-optimized",
            roles=["data"],
            extra_fields={
                "proj:epsg": 4326,
                "proj:shape": [height, width],
                "proj:transform": [1.0 / width, 0, 0, 0, -1.0 / height, 1, 0, 0, 1],
            },
        ),
    )
    item.add_asset(
        "schema-noise-vv",
        pystac.Asset(
            href=_NOISE_HREF, media_type="application/xml", roles=["metadata"]
        ),
    )
    return item


def test_derived_band_resolution_falls_back_to_sibling():
    item = _item_with_proj_only(width=100, height=50)

    with SimpleSTACReader(item) as src_dst:
        resolutions = _get_assets_resolutions(item, src_dst, bands=["vv_noise_lut"])

    assert "vv_noise_lut" in resolutions
    x_res, y_res, _crs = resolutions["vv_noise_lut"]
    assert x_res == pytest.approx(1.0 / 100)
    assert y_res == pytest.approx(1.0 / 50)


def test_derived_only_request_gets_a_sane_grid_not_the_1024_default():
    item = _item_with_proj_only(width=100, height=50)

    dims = _estimate_output_dimensions(
        [item], spatial_extent=None, bands=["vv_noise_lut"], check_max_pixels=False
    )

    assert (dims["width"], dims["height"]) != (1024, 1024)
    assert dims["width"] / dims["height"] == pytest.approx(100 / 50, rel=0.05)


def test_pixel_limit_counts_derived_bands(monkeypatch):
    """Requesting a derived band alongside the real one must count as 2
    bands for the pixel-limit check, not silently drop to 1.

    `_check_pixel_limit` builds its own `ProcessingSettings()` internally
    (not the module-level singleton), so the threshold is overridden via env
    var rather than monkeypatching an instance.
    """
    monkeypatch.setenv("TITILER_OPENEO_PROCESSING_MAX_PIXELS", "5000000")
    item = _item_with_proj_only(width=2000, height=2000)

    with pytest.raises(OutputLimitExceeded, match=r"x 2 bands"):
        _estimate_output_dimensions(
            [item],
            spatial_extent=None,
            bands=["vv", "vv_noise_lut"],
            check_max_pixels=True,
        )


# --------------------------------------------------------------------------- multi-item mosaic


def test_two_item_slice_mosaics_band_sources_without_rejection(tmp_path):
    """sar_backscatter rejects a slice mosaicking >1 item (sar.py:212) because
    calibration is inherently per item. A band-source read, resolved per item
    inside SimpleSTACReader.part() before mosaicking, has no such
    restriction -- this is the structural improvement ADR 0002 S2.3 argues
    for. Two *different* items, each with their own measurement+noise
    fixture, must both resolve and read without error.
    """
    measurement_path_1 = tmp_path / "measurement1.tif"
    measurement_path_2 = tmp_path / "measurement2.tif"
    _write_measurement_gcp_tiff(measurement_path_1)
    _write_measurement_gcp_tiff(measurement_path_2)

    noise_href_1 = "fixture://band-source-mosaic-noise-1"
    noise_href_2 = "fixture://band-source-mosaic-noise-2"
    item1 = _s1_item(str(measurement_path_1), noise_href=noise_href_1, item_id="s1-a")
    item2 = _s1_item(str(measurement_path_2), noise_href=noise_href_2, item_id="s1-b")

    fetcher = _FixtureFetcher({noise_href_1: _NOISE_XML, noise_href_2: _NOISE_XML})

    for item in (item1, item2):
        with SimpleSTACReader(item, band_source_fetcher=fetcher) as src_dst:
            img = src_dst.part(
                (0.0, 0.0, 1.0, 1.0),
                assets=["vv", "vv_noise_lut"],
                dst_crs=CRS.from_epsg(4326),
                bounds_crs=CRS.from_epsg(4326),
                width=2,
                height=2,
            )
            assert img.array.shape[0] == 2

    assert sorted(fetcher.calls) == sorted([noise_href_1, noise_href_2])
