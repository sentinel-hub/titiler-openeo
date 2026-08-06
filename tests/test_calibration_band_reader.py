"""Tests for `CalibrationBandReader` (issue #348, ADR 0002, increment 3).

Increments 1/2 (tests/test_band_sources_discovery.py,
tests/test_band_source_readers.py) covered discovery and `NoiseBandReader`.
This covers the remaining five bands one calibration annotation backs --
`<pol>_sigma0_lut`/`_beta0_lut`/`_gamma0_lut`/`_dn_lut`/
`_ellipsoid_incidence_angle` -- and the per-instance inverse-map memo that
becomes necessary now that one asset backs more than one band.

Fixture/fetcher/GCP-tiff setup mirrors test_band_source_readers.py exactly
(same rationale: `CalibrationBandReader.part()` only ever opens the
measurement asset header-only, for GCPs -- never its pixels).
"""

from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pystac
import pytest
import rasterio
from rasterio.control import GroundControlPoint
from rasterio.crs import CRS

from titiler.openeo.bandsources import readers as readers_module
from titiler.openeo.bandsources.readers import CalibrationBandReader
from titiler.openeo.reader import SimpleSTACReader
from titiler.openeo.sar import annotation, geocode

FIXTURES = Path(__file__).parent / "fixtures" / "sar"
_CALIBRATION_XML = (FIXTURES / "calibration_ipf290.xml").read_bytes()
_CALIBRATION_HREF = "fixture://calibration-band-reader-vv"

#: band-name suffix -> CalibrationLUT method name, mirroring sources.py.
QUANTITIES = {
    "sigma0_lut": "sigma_nought",
    "beta0_lut": "beta_nought",
    "gamma0_lut": "gamma",
    "dn_lut": "dn",
    "ellipsoid_incidence_angle": "ellipsoid_incidence_angle",
}


class _FixtureFetcher:
    """A fake AssetFetcher serving fixed bytes by href, with a call log."""

    def __init__(self, mapping: Dict[str, bytes]):
        self._mapping = mapping
        self.calls: List[str] = []

    def fetch(self, href: str) -> bytes:
        self.calls.append(href)
        return self._mapping[href]


def _write_measurement_gcp_tiff(path: Path) -> None:
    """Same GCP grid as test_sar_process.py/test_band_source_readers.py --
    lands a (0,0,1,1)-bounds destination grid inside the fixture LUT's real
    coordinate domain."""
    gcps = [
        GroundControlPoint(row=0, col=0, x=0, y=1),
        GroundControlPoint(row=0, col=12000, x=1, y=1),
        GroundControlPoint(row=8000, col=0, x=0, y=0),
        GroundControlPoint(row=8000, col=12000, x=1, y=0),
    ]
    with rasterio.open(
        path, "w", driver="GTiff", width=2, height=2, count=1, dtype="uint16"
    ) as dst:
        dst.write(np.full((2, 2), 100, dtype="uint16"), 1)
        dst.gcps = (gcps, CRS.from_epsg(4326))


def _s1_item(
    measurement_href: str,
    *,
    calibration_href: Optional[str] = _CALIBRATION_HREF,
    item_id: str = "s1test",
) -> pystac.Item:
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
    if calibration_href:
        item.add_asset(
            "schema-calibration-vv",
            pystac.Asset(
                href=calibration_href,
                media_type="application/xml",
                roles=["metadata"],
            ),
        )
    return item


# --------------------------------------------------------------------------- the oracle


@pytest.mark.parametrize("suffix,method_name", sorted(QUANTITIES.items()))
def test_calibration_band_matches_the_oracle(tmp_path, suffix, method_name):
    """Each of the five bands must equal `annotation.get_calibration(href).
    <method>(...)` at the same inverse-mapped coordinates `sar_backscatter`
    computes today, on the same grid."""
    measurement_path = tmp_path / "measurement.tif"
    _write_measurement_gcp_tiff(measurement_path)
    item = _s1_item(str(measurement_path))
    fetcher = _FixtureFetcher({_CALIBRATION_HREF: _CALIBRATION_XML})

    band_name = f"vv_{suffix}"
    with SimpleSTACReader(item, band_source_fetcher=fetcher) as src_dst:
        img = src_dst.part(
            (0.0, 0.0, 1.0, 1.0),
            assets=[band_name],
            dst_crs=CRS.from_epsg(4326),
            bounds_crs=CRS.from_epsg(4326),
            width=4,
            height=4,
        )

    gcps, gcp_crs = geocode.get_gcps(str(measurement_path))
    inverse = geocode.build_inverse_map(
        gcps, gcp_crs, 4, 4, (0.0, 0.0, 1.0, 1.0), CRS.from_epsg(4326)
    )
    calibration = annotation.get_calibration(_CALIBRATION_HREF, fetcher=fetcher)
    oracle = getattr(calibration, method_name)(inverse.line, inverse.pixel)

    np.testing.assert_allclose(img.array.data[0], oracle, rtol=1e-6)


def test_all_five_bands_together_each_match_their_own_oracle(tmp_path):
    """Requested together (the realistic case), not just one at a time --
    band order must not scramble which oracle applies to which output band."""
    measurement_path = tmp_path / "measurement.tif"
    _write_measurement_gcp_tiff(measurement_path)
    item = _s1_item(str(measurement_path))
    fetcher = _FixtureFetcher({_CALIBRATION_HREF: _CALIBRATION_XML})

    requested = [f"vv_{suffix}" for suffix in QUANTITIES]
    with SimpleSTACReader(item, band_source_fetcher=fetcher) as src_dst:
        img = src_dst.part(
            (0.0, 0.0, 1.0, 1.0),
            assets=requested,
            dst_crs=CRS.from_epsg(4326),
            bounds_crs=CRS.from_epsg(4326),
            width=4,
            height=4,
        )

    gcps, gcp_crs = geocode.get_gcps(str(measurement_path))
    inverse = geocode.build_inverse_map(
        gcps, gcp_crs, 4, 4, (0.0, 0.0, 1.0, 1.0), CRS.from_epsg(4326)
    )
    calibration = annotation.get_calibration(_CALIBRATION_HREF, fetcher=fetcher)

    assert img.array.shape[0] == len(requested)
    for idx, band_name in enumerate(requested):
        suffix = band_name[len("vv_") :]
        oracle = getattr(calibration, QUANTITIES[suffix])(inverse.line, inverse.pixel)
        np.testing.assert_allclose(img.array.data[idx], oracle, rtol=1e-6)


# --------------------------------------------------------------------------- the inverse-map memo


def test_inverse_map_built_once_for_five_bands_from_one_asset(tmp_path, monkeypatch):
    """The risk ADR 0002 flagged and increment 3 was expected to make real:
    five bands from the same calibration asset must build the TPS inverse
    map once, not five times -- SimpleSTACReader hands every derived-band
    reader constructed for one part() call the same inverse_map_cache dict.
    """
    measurement_path = tmp_path / "measurement.tif"
    _write_measurement_gcp_tiff(measurement_path)
    item = _s1_item(str(measurement_path))
    fetcher = _FixtureFetcher({_CALIBRATION_HREF: _CALIBRATION_XML})

    calls = []
    real_build = readers_module.geocode.build_inverse_map

    def counting_build(*args, **kwargs):
        calls.append(1)
        return real_build(*args, **kwargs)

    monkeypatch.setattr(readers_module.geocode, "build_inverse_map", counting_build)

    requested = [f"vv_{suffix}" for suffix in QUANTITIES]
    with SimpleSTACReader(item, band_source_fetcher=fetcher) as src_dst:
        src_dst.part(
            (0.0, 0.0, 1.0, 1.0),
            assets=requested,
            dst_crs=CRS.from_epsg(4326),
            bounds_crs=CRS.from_epsg(4326),
            width=4,
            height=4,
        )

    assert len(calls) == 1


def test_inverse_map_cache_does_not_leak_across_items(tmp_path, monkeypatch):
    """Two different items (two different SimpleSTACReader instances) must
    each build their own inverse map -- the memo is scoped to one instance,
    not accidentally shared via module state."""
    path_a = tmp_path / "measurement_a.tif"
    path_b = tmp_path / "measurement_b.tif"
    _write_measurement_gcp_tiff(path_a)
    _write_measurement_gcp_tiff(path_b)
    fetcher = _FixtureFetcher({_CALIBRATION_HREF: _CALIBRATION_XML})

    calls = []
    real_build = readers_module.geocode.build_inverse_map

    def counting_build(*args, **kwargs):
        calls.append(1)
        return real_build(*args, **kwargs)

    monkeypatch.setattr(readers_module.geocode, "build_inverse_map", counting_build)

    requested = ["vv_sigma0_lut", "vv_beta0_lut"]
    for path, item_id in ((path_a, "item-a"), (path_b, "item-b")):
        item = _s1_item(str(path), item_id=item_id)
        with SimpleSTACReader(item, band_source_fetcher=fetcher) as src_dst:
            src_dst.part(
                (0.0, 0.0, 1.0, 1.0),
                assets=requested,
                dst_crs=CRS.from_epsg(4326),
                bounds_crs=CRS.from_epsg(4326),
                width=4,
                height=4,
            )

    # One build per item (two total), not one shared across both.
    assert len(calls) == 2


# --------------------------------------------------------------------------- wiring


def test_get_reader_dispatches_calibration_bands(tmp_path):
    measurement_path = tmp_path / "measurement.tif"
    _write_measurement_gcp_tiff(measurement_path)
    item = _s1_item(str(measurement_path))

    with SimpleSTACReader(item) as src_dst:
        info = src_dst._get_asset_info("vv_sigma0_lut")
        assert src_dst._get_reader(info) is CalibrationBandReader
        assert info["reader_options"]["quantity"] == "sigma_nought"


def test_calibration_band_requires_a_quantity():
    """Constructing CalibrationBandReader without a quantity (a
    registry-wiring bug, not a user error) must fail clearly, not silently
    return the wrong thing."""
    reader = CalibrationBandReader("href", sibling_href="sibling", quantity=None)
    with pytest.raises(ValueError, match="quantity"):
        reader._evaluate(np.array([0.0]), np.array([0.0]))
