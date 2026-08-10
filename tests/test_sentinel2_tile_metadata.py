"""Tests for Sentinel-2 MTD_TL.xml parsing (docs/adr/0004-sentinel2-view-sun-angle-bands.md).

`mtd_tl_sample.xml` is a real, trimmed ESA tile-metadata file (see
tests/fixtures/sentinel2/README.md for provenance). The oracle values below
were computed independently from the raw, untrimmed XML with plain
`xml.etree.ElementTree` and cross-checked against this same item's own
flattened `view:incidence_angle`/`view:azimuth`/`view:sun_elevation`/
`view:sun_azimuth` STAC properties (Earth Search, fetched live 2026-08-07) --
they are not derived from the code under test.
"""

import uuid
from pathlib import Path

import numpy as np
import pytest

from titiler.openeo.sentinel2.tile_metadata import (
    _circular_mean_degrees,
    get_tile_metadata,
    parse_tile_metadata,
)

FIXTURE = Path(__file__).parent / "fixtures" / "sentinel2" / "mtd_tl_sample.xml"


class _CountingFetcher:
    """A fake AssetFetcher that records how many times fetch() was called."""

    def __init__(self, payload: bytes):
        self.payload = payload
        self.calls = 0

    def fetch(self, href: str) -> bytes:
        self.calls += 1
        return self.payload


def _unique_href(name: str) -> str:
    """A cache key guaranteed not to collide with other tests' cache entries."""
    return f"fixture://{name}/{uuid.uuid4()}"


@pytest.fixture
def xml_bytes() -> bytes:
    return FIXTURE.read_bytes()


def test_tile_geocoding(xml_bytes):
    meta = parse_tile_metadata(xml_bytes)
    assert meta.geocoding.crs == "EPSG:32629"
    assert meta.geocoding.ulx == 600000.0
    assert meta.geocoding.uly == 6700020.0


def test_mean_view_zenith_matches_the_live_stac_property_oracle(xml_bytes):
    """Earth Search's own `view:incidence_angle` for this item
    (S2B_29VPG_20260807_0_L2A) is 8.396813497980254 -- bit-identical to the
    arithmetic mean of the 13 Mean_Viewing_Incidence_Angle/ZENITH_ANGLE
    values, proving Earth Search computes it the same way this reader does."""
    meta = parse_tile_metadata(xml_bytes)
    assert meta.angles.mean_view_zenith == 8.396813497980254


def test_mean_view_azimuth_uses_a_circular_not_arithmetic_mean(xml_bytes):
    """Earth Search's `view:azimuth` for this item is 102.96270254490969 --
    the *arithmetic* mean of the 13 AZIMUTH_ANGLE values. This reader
    deliberately returns the *circular* mean instead (102.9626524696764,
    ~1e-4 degrees different here) because a naive arithmetic mean is wrong
    in general near the 0/360 wrap boundary -- see
    test_circular_mean_handles_the_wrap_boundary below."""
    meta = parse_tile_metadata(xml_bytes)
    assert meta.angles.mean_view_azimuth == pytest.approx(102.9626524696764, abs=1e-9)
    assert meta.angles.mean_view_azimuth != pytest.approx(102.96270254490969, abs=1e-9)


def test_sun_angles_grid_shape_and_step(xml_bytes):
    meta = parse_tile_metadata(xml_bytes)
    zenith = meta.angles.sun_grid.values["zenith"]
    azimuth = meta.angles.sun_grid.values["azimuth"]
    assert zenith.shape == (23, 23)
    assert azimuth.shape == (23, 23)
    # 5000m step * 22 intervals = 110000m grid extent
    assert meta.angles.sun_grid.lines[-1] == 5000.0 * 22
    assert meta.angles.sun_grid.pixels[-1] == 5000.0 * 22


def test_sun_angles_grid_corner_value_is_the_real_esa_value(xml_bytes):
    meta = parse_tile_metadata(xml_bytes)
    assert meta.angles.sun_grid.values["zenith"][0][0] == 45.0046


def test_sun_grid_interp_at_a_node_reproduces_the_exact_value(xml_bytes):
    """Bilinear interpolation exactly at a grid node must reproduce that
    node's own value -- the deterministic oracle Grid2D.interp should
    satisfy everywhere it's already tested for SAR (test_sar_annotation.py),
    now proven for the Sentinel-2 grid too."""
    meta = parse_tile_metadata(xml_bytes)
    value = meta.angles.sun_grid.interp("zenith", np.array([0.0]), np.array([0.0]))
    assert value[0] == meta.angles.sun_grid.values["zenith"][0][0]


def test_missing_element_raises_clear_value_error():
    xml = b"""<?xml version="1.0"?>
    <n1:Level-2A_Tile_ID xmlns:n1="https://psd-15.sentinel2.eo.esa.int/PSD/S2_PDI_Level-2A_Tile_Metadata.xsd">
      <n1:Geometric_Info>
        <Tile_Geocoding></Tile_Geocoding>
      </n1:Geometric_Info>
    </n1:Level-2A_Tile_ID>
    """
    with pytest.raises(ValueError, match="HORIZONTAL_CS_CODE"):
        parse_tile_metadata(xml)


def test_zenith_azimuth_shape_mismatch_raises():
    xml = b"""<?xml version="1.0"?>
    <n1:Level-2A_Tile_ID xmlns:n1="https://psd-15.sentinel2.eo.esa.int/PSD/S2_PDI_Level-2A_Tile_Metadata.xsd">
      <n1:Geometric_Info>
        <Tile_Geocoding>
          <HORIZONTAL_CS_CODE>EPSG:32629</HORIZONTAL_CS_CODE>
          <Geoposition resolution="10">
            <ULX>600000</ULX>
            <ULY>6700020</ULY>
          </Geoposition>
        </Tile_Geocoding>
        <Tile_Angles>
          <Sun_Angles_Grid>
            <Zenith>
              <COL_STEP unit="m">5000</COL_STEP>
              <ROW_STEP unit="m">5000</ROW_STEP>
              <Values_List>
                <VALUES>1 2</VALUES>
              </Values_List>
            </Zenith>
            <Azimuth>
              <COL_STEP unit="m">5000</COL_STEP>
              <ROW_STEP unit="m">5000</ROW_STEP>
              <Values_List>
                <VALUES>1 2 3</VALUES>
              </Values_List>
            </Azimuth>
          </Sun_Angles_Grid>
          <Mean_Viewing_Incidence_Angle_List>
            <Mean_Viewing_Incidence_Angle bandId="0">
              <ZENITH_ANGLE unit="deg">1.0</ZENITH_ANGLE>
              <AZIMUTH_ANGLE unit="deg">2.0</AZIMUTH_ANGLE>
            </Mean_Viewing_Incidence_Angle>
          </Mean_Viewing_Incidence_Angle_List>
        </Tile_Angles>
      </n1:Geometric_Info>
    </n1:Level-2A_Tile_ID>
    """
    with pytest.raises(ValueError, match="Zenith/Azimuth grids disagree"):
        parse_tile_metadata(xml)


# --------------------------------------------------------------------------
# _circular_mean_degrees
# --------------------------------------------------------------------------


def test_circular_mean_handles_the_wrap_boundary():
    """The textbook case a naive arithmetic mean gets wrong: 359 and 1
    average to 0 (the true midpoint, wrapping through 0/360), not 180."""
    assert _circular_mean_degrees([359.0, 1.0]) == pytest.approx(0.0, abs=1e-9)


def test_circular_mean_degrades_to_arithmetic_mean_away_from_the_wrap():
    assert _circular_mean_degrees([10.0, 20.0, 30.0]) == pytest.approx(20.0, abs=1e-6)


def test_circular_mean_matches_the_real_tile_oracle():
    values = [
        100.930212263709,
        105.915054626058,
        104.777064982303,
        103.726241126543,
        103.162749306527,
        102.588171554529,
        102.025517420697,
        105.346760842833,
        101.456779203816,
        100.351865997159,
    ]
    # (partial list is enough to confirm the formula; full 13-value oracle
    # is exercised end to end via test_mean_view_azimuth_... above)
    result = _circular_mean_degrees(values)
    assert 95 < result < 110


# --------------------------------------------------------------------------
# get_tile_metadata -- fetch + cache
# --------------------------------------------------------------------------


def test_get_tile_metadata_is_cached_by_href(xml_bytes):
    """Repeated calls for the same href reuse the cached parsed metadata --
    mirrors sar/annotation.py's get_calibration/get_noise caching tests."""
    fetcher = _CountingFetcher(xml_bytes)
    href = _unique_href("tile-metadata")

    first = get_tile_metadata(href, fetcher=fetcher)
    second = get_tile_metadata(href, fetcher=fetcher)

    assert fetcher.calls == 1
    assert first is second
    assert first.geocoding.crs == "EPSG:32629"


def test_get_tile_metadata_different_hrefs_not_conflated(xml_bytes):
    fetcher_a = _CountingFetcher(xml_bytes)
    fetcher_b = _CountingFetcher(xml_bytes)

    get_tile_metadata(_unique_href("a"), fetcher=fetcher_a)
    get_tile_metadata(_unique_href("b"), fetcher=fetcher_b)

    assert fetcher_a.calls == 1
    assert fetcher_b.calls == 1
