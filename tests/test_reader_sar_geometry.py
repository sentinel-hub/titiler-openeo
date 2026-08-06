"""Regression tests for issue #338.

Earth Search and Planetary Computer publish a `proj:epsg`/`proj:transform` for
Sentinel-1 GRD that is fabricated (bbox-derived) rather than measured: the actual
asset is GCP-referenced SAR geometry with no valid affine transform. Before this
fix, `SimpleSTACReader.__attrs_post_init__` trusted that metadata unconditionally,
so `load_collection` silently returned mis-georeferenced output with no error
raised (docs/adr/0001-sar-backscatter.md S1.7).

The fix keys off what the data actually says (GCPs, no CRS) rather than the STAC
metadata convention that varies per catalogue, gated by a cheap `sar:instrument_mode`
check so ordinary (non-SAR) items pay no extra I/O.
"""

import logging

import numpy as np
import pytest
import rasterio
from pystac import Item
from rasterio.control import GroundControlPoint
from rasterio.crs import CRS
from rio_tiler.constants import WGS84_CRS

from titiler.openeo.reader import (
    SimpleSTACReader,
    _is_asset_gcp_referenced,
    _item_has_untrustworthy_proj,
    _item_looks_like_sar,
)

# A fabricated bbox-derived affine, exactly like the ones Earth Search / Planetary
# Computer advertise for S1 GRD (ADR S1.7) -- wrong shape (transposed, like PC's) and
# a transform that has nothing to do with the real (GCP-referenced) asset below.
_FABRICATED_BBOX = [10.0, 40.0, 12.0, 42.0]


def _sar_item(assets: dict, proj: bool = True) -> Item:
    properties = {
        "datetime": "2025-01-01T00:00:00Z",
        "sar:instrument_mode": "IW",
        "sar:polarizations": ["VV", "VH"],
    }
    if proj:
        properties.update(
            {
                "proj:epsg": 4326,
                "proj:shape": [50, 100],
                "proj:transform": [0.02, 0, 10.0, 0, -0.02, 42.0, 0, 0, 1],
            }
        )
    return Item.from_dict(
        {
            "type": "Feature",
            "stac_version": "1.0.0",
            "stac_extensions": [
                "https://stac-extensions.github.io/projection/v1.1.0/schema.json",
                "https://stac-extensions.github.io/sar/v1.0.0/schema.json",
            ],
            "id": "s1-grd-test-item",
            "bbox": _FABRICATED_BBOX,
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[10, 40], [12, 40], [12, 42], [10, 42], [10, 40]]],
            },
            "properties": properties,
            "assets": assets,
        }
    )


@pytest.fixture
def gcp_tif(tmp_path):
    """A tiny GCP-referenced GeoTIFF: crs=None, a handful of real GCPs.

    Mirrors the georeferencing shape of a real Sentinel-1 GRD measurement TIFF
    (crs=None, identity transform, GCP grid in EPSG:4326) at a size a test can hold.
    """
    path = tmp_path / "measurement.tif"
    width, height = 8, 6
    gcps = [
        GroundControlPoint(row=0, col=0, x=10.0, y=42.0, z=0),
        GroundControlPoint(row=0, col=width - 1, x=11.9, y=41.95, z=0),
        GroundControlPoint(row=height - 1, col=0, x=10.05, y=40.05, z=0),
        GroundControlPoint(row=height - 1, col=width - 1, x=11.95, y=40.0, z=0),
    ]
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=width,
        height=height,
        count=1,
        dtype="uint16",
    ) as dst:
        dst.write(np.ones((height, width), dtype="uint16"), 1)
        dst.gcps = (gcps, CRS.from_epsg(4326))
    return path


@pytest.fixture
def georeferenced_tif(tmp_path):
    """A normal, properly georeferenced GeoTIFF (real CRS, no GCPs) -- the RTC case."""
    path = tmp_path / "rtc.tif"
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=8,
        height=6,
        count=1,
        dtype="uint16",
        crs="EPSG:4326",
        transform=rasterio.transform.from_bounds(10, 40, 12, 42, 8, 6),
    ) as dst:
        dst.write(np.ones((6, 8), dtype="uint16"), 1)
    return path


def test_item_looks_like_sar():
    """sar:instrument_mode is the cheap, no-I/O pre-filter."""
    assert _item_looks_like_sar(_sar_item({}))

    non_sar = Item.from_dict(
        {
            "type": "Feature",
            "stac_version": "1.0.0",
            "id": "optical-item",
            "bbox": [0, 0, 1, 1],
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]],
            },
            "properties": {"datetime": "2025-01-01T00:00:00Z"},
            "assets": {},
        }
    )
    assert not _item_looks_like_sar(non_sar)


def test_is_asset_gcp_referenced(gcp_tif, georeferenced_tif):
    assert _is_asset_gcp_referenced(str(gcp_tif))
    assert not _is_asset_gcp_referenced(str(georeferenced_tif))
    # Unreachable/unreadable asset: fail closed (not GCP-referenced), never raise.
    assert not _is_asset_gcp_referenced(str(gcp_tif.parent / "missing.tif"))


def test_untrustworthy_proj_skips_non_sar_items_without_any_io(monkeypatch, gcp_tif):
    """Non-SAR items never pay for the header open, regardless of asset content."""

    def _boom(*args, **kwargs):
        raise AssertionError("rasterio.open must not be called for non-SAR items")

    monkeypatch.setattr(rasterio, "open", _boom)

    item = _sar_item({"vv": {"href": str(gcp_tif)}})
    item.properties.pop("sar:instrument_mode")
    assert not _item_has_untrustworthy_proj(item, ["vv"])


def test_untrustworthy_proj_skips_non_raster_assets(gcp_tif):
    """Annotation/manifest siblings (xml/json) are never opened."""
    item = _sar_item(
        {
            "schema-calibration-vv": {"href": "https://example.com/cal.xml"},
            "vv": {"href": str(gcp_tif)},
        }
    )
    assert _item_has_untrustworthy_proj(item, ["schema-calibration-vv", "vv"])


def test_untrustworthy_proj_trusts_real_georeferencing(georeferenced_tif):
    """A geocoded SAR product (e.g. RTC) with a real CRS keeps its proj:* metadata."""
    item = _sar_item({"vv": {"href": str(georeferenced_tif)}})
    assert not _item_has_untrustworthy_proj(item, ["vv"])


def test_simple_stac_reader_ignores_fabricated_proj_for_gcp_referenced_asset(
    gcp_tif, caplog
):
    """The end-to-end regression: SimpleSTACReader must not adopt the fabricated
    proj:epsg/transform/shape when the asset is GCP-referenced SAR geometry, and
    must fall back to the item's footprint bbox instead -- exactly as it already
    does for catalogues (e.g. CDSE) that publish no proj:* at all.
    """
    item = _sar_item({"vv": {"href": str(gcp_tif)}})

    with caplog.at_level(logging.WARNING, logger="titiler.openeo.reader"):
        with SimpleSTACReader(item) as src_dst:
            assert src_dst.crs == WGS84_CRS
            assert tuple(src_dst.bounds) == tuple(_FABRICATED_BBOX)

    assert "Ignoring STAC" in caplog.text


def test_simple_stac_reader_keeps_proj_when_not_gcp_referenced(georeferenced_tif):
    """Control: a SAR item whose asset genuinely has a CRS keeps its proj:* metadata."""
    item = _sar_item({"vv": {"href": str(georeferenced_tif)}})

    with SimpleSTACReader(item) as src_dst:
        assert src_dst.crs.to_epsg() == 4326
        assert src_dst.width == 100
        assert src_dst.height == 50
