"""Tests for Sentinel-2 view/sun angle band readers (docs/adr/0004-sentinel2-view-sun-angle-bands.md).

Mirrors tests/test_band_source_readers.py's shape: `ViewAngleMeanReader`/
`SunAngleGridReader` end to end through `SimpleSTACReader`'s
`_get_asset_info`/`_get_reader` hooks, mask inheritance, resolution
fallback for a derived-only request, and multi-item mosaicking.

Unlike Sentinel-1's readers, these need no GCPs -- Sentinel-2 imagery has
ordinary affine georeferencing, so the "sibling" (real raster band) fixture
here is a plain georeferenced GeoTIFF, not a GCP-warped one.
"""

from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pystac
import pytest
import rasterio
from rasterio.crs import CRS
from rasterio.transform import from_bounds
from rio_tiler.errors import InvalidAssetName

from titiler.openeo.bandsources.sentinel2_readers import (
    SunAngleGridReader,
    ViewAngleMeanReader,
)
from titiler.openeo.reader import SimpleSTACReader, _get_assets_resolutions
from titiler.openeo.sentinel2.tile_metadata import parse_tile_metadata

FIXTURES = Path(__file__).parent / "fixtures" / "sentinel2"
_MTD_TL_XML = (FIXTURES / "mtd_tl_sample.xml").read_bytes()
_ORACLE = parse_tile_metadata(_MTD_TL_XML)
_GRANULE_METADATA_HREF = "fixture://sentinel2-angle-readers-mtd-tl"

# The trimmed fixture's own tile: EPSG:32629, ULX=600000, ULY=6700020.
_TILE_CRS = CRS.from_epsg(32629)


class _FixtureFetcher:
    """A fake AssetFetcher serving fixed bytes by href, with a call log."""

    def __init__(self, mapping: Dict[str, bytes]):
        self._mapping = mapping
        self.calls: List[str] = []

    def fetch(self, href: str) -> bytes:
        self.calls.append(href)
        return self._mapping[href]


def _write_red_band_tiff(path: Path) -> None:
    """A small, real, ordinarily-georeferenced (no GCPs) GeoTIFF inside the
    fixture tile's own extent, mirroring the B04_10m/red/B04 asset every
    catalogue has -- gsd is what pick_nominal_sibling_by_resolution keys on.
    """
    bounds = (600000.0, 6699980.0, 600040.0, 6700020.0)  # 40m x 40m at 10m/px
    transform = from_bounds(*bounds, 4, 4)
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=4,
        height=4,
        count=1,
        dtype="uint16",
        crs=_TILE_CRS,
        transform=transform,
    ) as dst:
        dst.write(np.full((4, 4), 1000, dtype="uint16"), 1)


def _s2_item(
    red_href: str,
    *,
    granule_metadata_href: Optional[str] = _GRANULE_METADATA_HREF,
    item_id: str = "s2test",
) -> pystac.Item:
    """A minimal Sentinel-2 L2A item: one raster band + granule metadata."""
    item = pystac.Item(
        id=item_id,
        geometry={
            "type": "Polygon",
            "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]],
        },
        bbox=[0, 0, 1, 1],
        datetime=datetime(2024, 1, 1),
        properties={},
        collection="sentinel-2-l2a",
    )
    item.add_asset(
        "B04_10m",
        pystac.Asset(
            href=red_href,
            media_type="image/tiff; application=geotiff; profile=cloud-optimized",
            roles=["data", "reflectance"],
            extra_fields={"gsd": 10},
        ),
    )
    if granule_metadata_href:
        item.add_asset(
            "granule_metadata",
            pystac.Asset(
                href=granule_metadata_href,
                media_type="application/xml",
                roles=["metadata"],
            ),
        )
    return item


_BBOX = (600000.0, 6699980.0, 600040.0, 6700020.0)


# --------------------------------------------------------------------------- the oracle


def test_sun_zenith_grid_matches_the_tile_metadata_oracle(tmp_path):
    """sunZenithAngles must equal Grid2D.interp on the same destination
    grid the reader itself computes -- the direct reference this feature's
    geometry (reproject into tile CRS, convert to grid row/col units) was
    built to reproduce."""
    red_path = tmp_path / "red.tif"
    _write_red_band_tiff(red_path)
    item = _s2_item(str(red_path))
    fetcher = _FixtureFetcher({_GRANULE_METADATA_HREF: _MTD_TL_XML})

    with SimpleSTACReader(item, band_source_fetcher=fetcher) as src_dst:
        img = src_dst.part(
            _BBOX,
            assets=["sunZenithAngles"],
            dst_crs=_TILE_CRS,
            bounds_crs=_TILE_CRS,
            width=4,
            height=4,
        )

    row_units = _ORACLE.geocoding.uly - _BBOX[3]
    col_units = _BBOX[0] - _ORACLE.geocoding.ulx
    oracle_corner = _ORACLE.angles.sun_grid.interp(
        "zenith", np.array([row_units]), np.array([col_units])
    )[0]

    # Top-left destination pixel's centre is within one pixel (10m) of the
    # bbox's own top-left corner -- close enough that the grid (5000m
    # spacing) barely moves, so this is a meaningful, not vacuous, check.
    np.testing.assert_allclose(img.array.data[0, 0, 0], oracle_corner, rtol=1e-3)


def test_view_zenith_mean_matches_the_tile_metadata_oracle(tmp_path):
    red_path = tmp_path / "red.tif"
    _write_red_band_tiff(red_path)
    item = _s2_item(str(red_path))
    fetcher = _FixtureFetcher({_GRANULE_METADATA_HREF: _MTD_TL_XML})

    with SimpleSTACReader(item, band_source_fetcher=fetcher) as src_dst:
        img = src_dst.part(
            _BBOX,
            assets=["viewZenithMean"],
            dst_crs=_TILE_CRS,
            bounds_crs=_TILE_CRS,
            width=2,
            height=2,
        )

    assert np.all(img.array.data[0] == _ORACLE.angles.mean_view_zenith)


def test_view_azimuth_mean_matches_the_tile_metadata_oracle(tmp_path):
    red_path = tmp_path / "red.tif"
    _write_red_band_tiff(red_path)
    item = _s2_item(str(red_path))
    fetcher = _FixtureFetcher({_GRANULE_METADATA_HREF: _MTD_TL_XML})

    with SimpleSTACReader(item, band_source_fetcher=fetcher) as src_dst:
        img = src_dst.part(
            _BBOX,
            assets=["viewAzimuthMean"],
            dst_crs=_TILE_CRS,
            bounds_crs=_TILE_CRS,
            width=2,
            height=2,
        )

    assert np.all(img.array.data[0] == _ORACLE.angles.mean_view_azimuth)


def test_constructing_the_reader_triggers_no_fetch(tmp_path):
    """Resolving derived bands at __attrs_post_init__ time is pure/no-I/O --
    constructing SimpleSTACReader must not itself call the fetcher."""
    red_path = tmp_path / "red.tif"
    _write_red_band_tiff(red_path)
    item = _s2_item(str(red_path))
    fetcher = _FixtureFetcher({_GRANULE_METADATA_HREF: _MTD_TL_XML})

    with SimpleSTACReader(item, band_source_fetcher=fetcher) as src_dst:
        for band in (
            "viewZenithMean",
            "viewAzimuthMean",
            "sunZenithAngles",
            "sunAzimuthAngles",
        ):
            assert band in src_dst._derived_bands
        assert fetcher.calls == []


# --------------------------------------------------------------------------- wiring


def test_get_reader_dispatches_the_two_reader_classes(tmp_path):
    red_path = tmp_path / "red.tif"
    _write_red_band_tiff(red_path)
    item = _s2_item(str(red_path))

    with SimpleSTACReader(item) as src_dst:
        view_info = src_dst._get_asset_info("viewZenithMean")
        sun_info = src_dst._get_asset_info("sunZenithAngles")
        real_info = src_dst._get_asset_info("B04_10m")

        assert src_dst._get_reader(view_info) is ViewAngleMeanReader
        assert src_dst._get_reader(sun_info) is SunAngleGridReader
        assert src_dst._get_reader(real_info) is src_dst.reader


def test_derived_asset_info_resolves_the_dynamic_sibling(tmp_path):
    """Unlike Sentinel-1's fixed `"{pol}"` template, the sibling here is
    picked dynamically from the item's own assets by gsd -- must resolve to
    the one real raster band present."""
    red_path = tmp_path / "red.tif"
    _write_red_band_tiff(red_path)
    item = _s2_item(str(red_path))

    with SimpleSTACReader(item) as src_dst:
        info = src_dst._get_asset_info("sunZenithAngles")

    assert info["url"] == _GRANULE_METADATA_HREF
    assert info["reader_options"]["sibling_href"] == str(red_path)


def test_no_eligible_sibling_still_resolves_without_error(tmp_path):
    """A derived-only item (no real raster band at all) must still resolve
    the band -- pick_nominal_sibling_by_resolution returning None is not an
    error, unlike Sentinel-1's fixed-template case where a missing sibling
    raises. There is simply nothing to inherit a resolution/mask from."""
    item = pystac.Item(
        id="s2-no-raster",
        geometry={
            "type": "Polygon",
            "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]],
        },
        bbox=[0, 0, 1, 1],
        datetime=datetime(2024, 1, 1),
        properties={},
        collection="sentinel-2-l2a",
    )
    item.add_asset(
        "granule_metadata",
        pystac.Asset(
            href=_GRANULE_METADATA_HREF,
            media_type="application/xml",
            roles=["metadata"],
        ),
    )

    with SimpleSTACReader(item) as src_dst:
        info = src_dst._get_asset_info("viewZenithMean")

    assert info["url"] == _GRANULE_METADATA_HREF
    assert "sibling_href" not in info["reader_options"]


def test_unrelated_xml_metadata_asset_is_not_mistaken_for_granule_metadata(tmp_path):
    """product_metadata is application/xml + metadata, same as
    granule_metadata -- must not resolve as the source for these bands."""
    red_path = tmp_path / "red.tif"
    _write_red_band_tiff(red_path)
    item = _s2_item(str(red_path), granule_metadata_href=None)
    item.add_asset(
        "product_metadata",
        pystac.Asset(
            href="fixture://unrelated-product-metadata",
            media_type="application/xml",
            roles=["metadata"],
        ),
    )

    with SimpleSTACReader(item) as src_dst:
        assert "viewZenithMean" not in src_dst._derived_bands
        with pytest.raises(InvalidAssetName):
            src_dst._get_asset_info("viewZenithMean")


# --------------------------------------------------------------------------- mask inheritance


def test_end_to_end_mask_inheritance_through_simple_stac_reader(tmp_path):
    """Through the real read path: a derived band's honestly-unmasked
    values must inherit its sibling's mask when read alongside it (ADR
    0002 S2.4 rule 1), the same trap S1's own equivalent test guards."""
    red_path = tmp_path / "red.tif"
    _write_red_band_tiff(red_path)
    item = _s2_item(str(red_path))
    fetcher = _FixtureFetcher({_GRANULE_METADATA_HREF: _MTD_TL_XML})

    with SimpleSTACReader(item, band_source_fetcher=fetcher) as src_dst:
        img = src_dst.part(
            _BBOX,
            assets=["B04_10m", "viewZenithMean"],
            dst_crs=_TILE_CRS,
            bounds_crs=_TILE_CRS,
            width=4,
            height=4,
        )
        from titiler.openeo.reader import _inherit_derived_band_masks

        img = _inherit_derived_band_masks(
            img, src_dst._derived_bands, ["B04_10m", "viewZenithMean"]
        )

    np.testing.assert_array_equal(img.array.mask[0], img.array.mask[1])


# --------------------------------------------------------------------------- resolution accounting


def _item_with_proj_only(width: int = 100, height: int = 50) -> pystac.Item:
    """An item (no rasterio I/O needed) whose B04_10m asset carries
    proj:shape/proj:transform, so resolution comes from STAC metadata alone
    -- mirrors test_band_source_readers.py's own helper of the same name."""
    item = pystac.Item(
        id="s2-proj-only",
        geometry={
            "type": "Polygon",
            "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]],
        },
        bbox=[0, 0, 1, 1],
        datetime=datetime(2024, 1, 1),
        properties={},
        collection="sentinel-2-l2a",
        stac_extensions=[
            "https://stac-extensions.github.io/projection/v1.1.0/schema.json"
        ],
    )
    item.add_asset(
        "B04_10m",
        pystac.Asset(
            href="https://example.com/B04_10m.jp2",
            media_type="image/jp2",
            roles=["data", "reflectance"],
            extra_fields={
                "gsd": 10,
                "proj:epsg": 4326,
                "proj:shape": [height, width],
                "proj:transform": [1.0 / width, 0, 0, 0, -1.0 / height, 1, 0, 0, 1],
            },
        ),
    )
    item.add_asset(
        "granule_metadata",
        pystac.Asset(
            href=_GRANULE_METADATA_HREF,
            media_type="application/xml",
            roles=["metadata"],
        ),
    )
    return item


def test_derived_band_resolution_falls_back_to_the_picked_sibling():
    item = _item_with_proj_only(width=100, height=50)

    with SimpleSTACReader(item) as src_dst:
        resolutions = _get_assets_resolutions(item, src_dst, bands=["viewZenithMean"])

    assert "viewZenithMean" in resolutions
    x_res, y_res, _crs = resolutions["viewZenithMean"]
    assert x_res == pytest.approx(1.0 / 100)
    assert y_res == pytest.approx(1.0 / 50)


# --------------------------------------------------------------------------- multi-item mosaic


def test_two_item_slice_reads_band_sources_independently(tmp_path):
    """Two different items, each with their own raster+metadata fixture,
    must both resolve and read without error -- the read is inherently
    per-item already (SimpleSTACReader operates on one item), so this
    mainly guards against any accidental cross-item state (e.g. a
    module-global cache keyed too coarsely)."""
    red_path_1 = tmp_path / "red1.tif"
    red_path_2 = tmp_path / "red2.tif"
    _write_red_band_tiff(red_path_1)
    _write_red_band_tiff(red_path_2)

    metadata_href_1 = "fixture://sentinel2-mosaic-mtd-1"
    metadata_href_2 = "fixture://sentinel2-mosaic-mtd-2"
    item1 = _s2_item(
        str(red_path_1), granule_metadata_href=metadata_href_1, item_id="s2-a"
    )
    item2 = _s2_item(
        str(red_path_2), granule_metadata_href=metadata_href_2, item_id="s2-b"
    )

    fetcher = _FixtureFetcher(
        {metadata_href_1: _MTD_TL_XML, metadata_href_2: _MTD_TL_XML}
    )

    for item in (item1, item2):
        with SimpleSTACReader(item, band_source_fetcher=fetcher) as src_dst:
            img = src_dst.part(
                _BBOX,
                assets=["B04_10m", "sunZenithAngles"],
                dst_crs=_TILE_CRS,
                bounds_crs=_TILE_CRS,
                width=2,
                height=2,
            )
            assert img.array.shape[0] == 2

    assert sorted(fetcher.calls) == sorted([metadata_href_1, metadata_href_2])
