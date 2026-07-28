"""Tests for titiler.openeo.sar.annotation."""

import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pytest

from titiler.openeo.sar.annotation import (
    Grid2D,
    get_calibration,
    get_noise,
    parse_calibration,
    parse_noise,
)

FIXTURES = Path(__file__).parent / "fixtures" / "sar"


class _CountingFetcher:
    """A fake AssetFetcher that records how many times fetch() was called."""

    def __init__(self, payload: bytes):
        self.payload = payload
        self.calls = 0

    def fetch(self, href: str) -> bytes:
        """Return the fixed payload, counting invocations."""
        self.calls += 1
        return self.payload


def _unique_href(name: str) -> str:
    """A cache key guaranteed not to collide with other tests' cache entries."""
    return f"fixture://{name}/{uuid.uuid4()}"


# --------------------------------------------------------------------------- Grid2D


def test_grid2d_bilinear_hand_computed():
    """Bilinear interpolation against hand-computed corner/midpoint values."""
    grid = Grid2D(
        lines=np.array([0.0, 10.0]),
        pixels=np.array([0.0, 10.0]),
        values={"v": np.array([[0.0, 10.0], [20.0, 30.0]])},
    )

    # Exact corners.
    assert grid.interp("v", np.array([0.0]), np.array([0.0]))[0] == pytest.approx(0.0)
    assert grid.interp("v", np.array([0.0]), np.array([10.0]))[0] == pytest.approx(10.0)
    assert grid.interp("v", np.array([10.0]), np.array([0.0]))[0] == pytest.approx(20.0)
    assert grid.interp("v", np.array([10.0]), np.array([10.0]))[0] == pytest.approx(
        30.0
    )

    # Centre is the average of all four corners.
    assert grid.interp("v", np.array([5.0]), np.array([5.0]))[0] == pytest.approx(15.0)

    # Off-centre point, computed by hand: tl=0.3, tp=0.7
    # v = 0*(0.7)*(0.3) + 10*(0.7)*(0.7) + 20*(0.3)*(0.3) + 30*(0.3)*(0.7)
    expected = 0 * 0.7 * 0.3 + 10 * 0.7 * 0.7 + 20 * 0.3 * 0.3 + 30 * 0.3 * 0.7
    got = grid.interp("v", np.array([3.0]), np.array([7.0]))[0]
    assert got == pytest.approx(expected)


def test_grid2d_clamps_outside_grid():
    """Queries outside the grid extent are clamped to the nearest edge cell."""
    grid = Grid2D(
        lines=np.array([0.0, 10.0]),
        pixels=np.array([0.0, 10.0]),
        values={"v": np.array([[0.0, 10.0], [20.0, 30.0]])},
    )
    below = grid.interp("v", np.array([-100.0]), np.array([-100.0]))[0]
    above = grid.interp("v", np.array([1000.0]), np.array([1000.0]))[0]
    assert below == pytest.approx(0.0)
    assert above == pytest.approx(30.0)


# --------------------------------------------------------------------------- calibration


@pytest.mark.parametrize(
    "fixture_name", ["calibration_ipf290.xml", "calibration_legacy.xml"]
)
def test_parse_calibration_fixtures(fixture_name):
    """Real (trimmed) ESA calibration annotations parse into sane LUTs."""
    xml = (FIXTURES / fixture_name).read_bytes()
    cal = parse_calibration(xml)

    assert cal.grid.lines.ndim == 1
    assert len(cal.grid.lines) >= 2
    for name in ("sigmaNought", "betaNought", "gamma", "dn"):
        assert cal.grid.values[name].shape == (
            len(cal.grid.lines),
            len(cal.grid.pixels),
        )

    mid_line = np.array([cal.grid.lines[len(cal.grid.lines) // 2]])
    mid_pixel = np.array([cal.grid.pixels[len(cal.grid.pixels) // 2]])

    # Sanity range: Sentinel-1 calibration LUT values are on the order of
    # hundreds, never negative or absurdly large.
    for getter in (cal.sigma_nought, cal.beta_nought, cal.gamma):
        value = getter(mid_line, mid_pixel)[0]
        assert 0 < value < 1e5


def test_calibration_lut_incidence_angle_identity():
    """The incidence angle derived from gamma/sigma agrees with the one from beta/sigma.

    ESA's calibration note defines both A_gamma and A_beta in terms of the
    same incidence angle (ADR S1.6d), so the two independent derivations
    below must agree even though the test never assumes a value for theta.
    """
    xml = (FIXTURES / "calibration_ipf290.xml").read_bytes()
    cal = parse_calibration(xml)

    line = cal.grid.lines
    pixel = cal.grid.pixels[: len(line)]  # arbitrary paired coordinates

    theta_from_gamma_sigma = cal.ellipsoid_incidence_angle(line, pixel)

    a_beta = cal.beta_nought(line, pixel)
    a_sigma = cal.sigma_nought(line, pixel)
    theta_from_beta_sigma = np.degrees(
        np.arcsin(np.clip((a_beta / a_sigma) ** 2, -1.0, 1.0))
    )

    np.testing.assert_allclose(theta_from_gamma_sigma, theta_from_beta_sigma, atol=0.01)
    # And the angles should be physically plausible for Sentinel-1 (IW: ~29-46 deg).
    assert np.all((0 < theta_from_gamma_sigma) & (theta_from_gamma_sigma < 90))


def test_parse_calibration_missing_element_raises():
    """A calibration vector missing a required LUT raises a clear error."""
    xml = b"""<?xml version="1.0"?>
    <calibration>
      <calibrationVectorList count="1">
        <calibrationVector>
          <line>0</line>
          <pixel count="2">0 10</pixel>
          <sigmaNought count="2">1 2</sigmaNought>
        </calibrationVector>
      </calibrationVectorList>
    </calibration>
    """
    with pytest.raises(ValueError, match="betaNought"):
        parse_calibration(xml)


def test_parse_calibration_ragged_grid_raises():
    """A LUT whose sample count disagrees with <pixel> raises a clear error."""
    xml = b"""<?xml version="1.0"?>
    <calibration>
      <calibrationVectorList count="1">
        <calibrationVector>
          <line>0</line>
          <pixel count="2">0 10</pixel>
          <sigmaNought count="2">1 2</sigmaNought>
          <betaNought count="1">1</betaNought>
          <gamma count="2">1 2</gamma>
          <dn count="2">1 2</dn>
        </calibrationVector>
      </calibrationVectorList>
    </calibration>
    """
    with pytest.raises(ValueError, match="[Rr]agged"):
        parse_calibration(xml)


def test_parse_calibration_empty_raises():
    """An annotation with no calibrationVector at all raises a clear error."""
    with pytest.raises(ValueError, match="calibrationVector"):
        parse_calibration(b"<calibration><calibrationVectorList/></calibration>")


# --------------------------------------------------------------------------- noise


def test_parse_noise_modern_schema_has_azimuth_blocks():
    """IPF >= 2.90 products expose noiseRangeVectorList + noiseAzimuthVectorList."""
    xml = (FIXTURES / "noise_ipf290.xml").read_bytes()
    noise = parse_noise(xml)
    assert len(noise.azimuth_blocks) > 0


def test_parse_noise_legacy_schema_has_no_azimuth_blocks():
    """IPF < 2.90 products use noiseVectorList and have no azimuth descalloping."""
    xml = (FIXTURES / "noise_legacy.xml").read_bytes()
    noise = parse_noise(xml)
    assert noise.azimuth_blocks == []
    # Still evaluable: the range LUT alone is the complete noise estimate.
    value = noise.evaluate(
        np.array([noise.range_grid.lines[0]]), np.array([noise.range_grid.pixels[0]])
    )
    assert value[0] >= 0


def test_noise_azimuth_scaling_actually_applied():
    """The azimuth LUT must multiply the range LUT, not be silently skipped.

    Regression guard: evaluate() at a point covered by a non-unity azimuth
    block must differ from the unscaled range-only value.
    """
    xml = (FIXTURES / "noise_ipf290.xml").read_bytes()
    noise = parse_noise(xml)
    assert noise.azimuth_blocks, "fixture must have at least one azimuth block"

    blk = noise.azimuth_blocks[0]
    line = np.array([(blk.first_line + blk.last_line) / 2])
    pixel = np.array([(blk.first_sample + blk.last_sample) / 2])

    scaled = noise.evaluate(line, pixel)[0]
    unscaled = noise.range_grid.interp("noiseRangeLut", line, pixel)[0]

    scale_factor = np.interp(line[0], blk.lines, blk.lut)
    assert scaled == pytest.approx(unscaled * scale_factor)
    # The fixture's azimuth LUT is not flat, so this also proves the
    # multiplication actually changes the value rather than being a no-op.
    if not np.allclose(blk.lut, blk.lut[0]):
        assert scaled != pytest.approx(unscaled)


def test_parse_noise_unrecognised_schema_raises():
    """An annotation with neither known noise list raises a clear error."""
    with pytest.raises(ValueError, match="noiseRangeVectorList|noiseVectorList"):
        parse_noise(b"<noise><somethingElse/></noise>")


# --------------------------------------------------------------------------- caching


def test_get_calibration_is_cached_by_href():
    """Repeated calls for the same href reuse the cached parsed LUT."""
    xml = (FIXTURES / "calibration_ipf290.xml").read_bytes()
    fetcher = _CountingFetcher(xml)
    href = _unique_href("calibration")

    first = get_calibration(href, fetcher=fetcher)
    second = get_calibration(href, fetcher=fetcher)

    assert fetcher.calls == 1
    assert first is second


def test_get_noise_is_cached_by_href():
    """Repeated calls for the same href reuse the cached parsed LUT."""
    xml = (FIXTURES / "noise_ipf290.xml").read_bytes()
    fetcher = _CountingFetcher(xml)
    href = _unique_href("noise")

    first = get_noise(href, fetcher=fetcher)
    second = get_noise(href, fetcher=fetcher)

    assert fetcher.calls == 1
    assert first is second


def test_get_calibration_different_hrefs_not_conflated():
    """Different hrefs are cached independently."""
    xml = (FIXTURES / "calibration_ipf290.xml").read_bytes()
    fetcher_a = _CountingFetcher(xml)
    fetcher_b = _CountingFetcher(xml)

    get_calibration(_unique_href("a"), fetcher=fetcher_a)
    get_calibration(_unique_href("b"), fetcher=fetcher_b)

    assert fetcher_a.calls == 1
    assert fetcher_b.calls == 1


def test_get_calibration_concurrent_access_fetches_once():
    """Concurrent requests for the same href only trigger one underlying fetch.

    RasterStack executes tasks on a thread pool (ADR S7.5), so the cache
    must be safe -- and effective -- under concurrent access, not just
    single-threaded reuse.
    """
    xml = (FIXTURES / "calibration_ipf290.xml").read_bytes()
    fetcher = _CountingFetcher(xml)
    href = _unique_href("concurrent")

    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(
            pool.map(lambda _: get_calibration(href, fetcher=fetcher), range(32))
        )

    assert fetcher.calls == 1
    assert all(r is results[0] for r in results)
