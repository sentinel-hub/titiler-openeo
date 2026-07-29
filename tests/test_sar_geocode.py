"""Tests for titiler.openeo.sar.geocode.

geocode.py builds the destination-to-source inverse map only -- it does not read
pixels (docs/adr/0001-sar-backscatter.md S7.3, S7.10). These tests cover the map
itself: hand-computed values on a trivial affine-consistent GCP set (with and
without a dst_crs -> gcp_crs reprojection), and the TPS round-trip accuracy
acceptance criterion (ADR S7.8.4) on a real Sentinel-1 GCP grid.
"""

import json
from pathlib import Path

import numpy as np
from rasterio.control import GroundControlPoint
from rasterio.crs import CRS
from rasterio.warp import transform as warp_transform

from titiler.openeo.sar.geocode import build_inverse_map

FIXTURE = Path(__file__).parent / "fixtures" / "sar" / "gcps_ew_grdm_polar.json"

# Four corner GCPs consistent with the exact affine row = 10 - y, col = x, so
# TPS (which reproduces any affine-consistent input exactly) gives hand-computable
# results independent of the CRS path taken to get there.
_AFFINE_GCPS = [
    GroundControlPoint(row=0, col=0, x=0, y=10),
    GroundControlPoint(row=0, col=10, x=10, y=10),
    GroundControlPoint(row=10, col=0, x=0, y=0),
    GroundControlPoint(row=10, col=10, x=10, y=0),
]

# Expected inverse map for a 2x2 destination grid over bounds (0, 0, 10, 10):
# pixel centres sit at ground (2.5, 7.5) and (7.5, 2.5)/(7.5, 7.5) etc., and
# row = 10 - y, col = x gives these directly.
_EXPECTED_LINE = np.array([[2.5, 2.5], [7.5, 7.5]])
_EXPECTED_PIXEL = np.array([[2.5, 7.5], [2.5, 7.5]])


def test_build_inverse_map_hand_computed_identity_crs():
    """No reprojection needed when dst_crs == gcp_crs."""
    crs = CRS.from_epsg(4326)
    inverse = build_inverse_map(
        _AFFINE_GCPS, crs, width=2, height=2, bounds=(0, 0, 10, 10), dst_crs=crs
    )

    np.testing.assert_allclose(inverse.line, _EXPECTED_LINE)
    np.testing.assert_allclose(inverse.pixel, _EXPECTED_PIXEL)


def test_build_inverse_map_reprojects_dst_crs_to_gcp_crs():
    """dst_crs != gcp_crs: destination pixel centres must be reprojected first.

    Reuses the same affine-consistent GCPs and expected values as the identity
    case, but places the tiny ground square at a real (lon, lat) location and
    drives the destination grid in Web Mercator -- exercising the
    dst_crs -> gcp_crs warp_transform branch. At this scale (~100 m) Mercator
    distortion is negligible, so the recovered (line, pixel) should match the
    identity-CRS case to a small fraction of a pixel; a bug that skipped
    reprojection would instead feed raw Web Mercator meters into the GCP
    transformer and produce wildly out-of-range results.
    """
    lon0, lat0, delta = 10.0, 45.0, 0.001
    gcps = [
        GroundControlPoint(row=0, col=0, x=lon0, y=lat0 + delta),
        GroundControlPoint(row=0, col=10, x=lon0 + delta, y=lat0 + delta),
        GroundControlPoint(row=10, col=0, x=lon0, y=lat0),
        GroundControlPoint(row=10, col=10, x=lon0 + delta, y=lat0),
    ]
    gcp_crs = CRS.from_epsg(4326)
    dst_crs = CRS.from_epsg(3857)

    xs, ys = warp_transform(
        gcp_crs, dst_crs, [lon0, lon0 + delta], [lat0, lat0 + delta]
    )
    bounds = (xs[0], ys[0], xs[1], ys[1])

    inverse = build_inverse_map(
        gcps, gcp_crs, width=2, height=2, bounds=bounds, dst_crs=dst_crs
    )

    np.testing.assert_allclose(inverse.line, _EXPECTED_LINE, atol=1e-2)
    np.testing.assert_allclose(inverse.pixel, _EXPECTED_PIXEL, atol=1e-2)


def test_tps_round_trip_on_real_polar_gcps_sub_metre():
    """Acceptance criterion 4 (ADR S7.8): TPS round-trip residual < 1 m RMS.

    Regression guard for "TPS really is in use" -- this is what would have
    caught both an order-2 default and an ignored METHOD=GCP_TPS (ADR S1.6b).
    Probes each GCP with its own 1x1 destination "pixel" centred exactly on
    that GCP's ground coordinate, so the recovered (line, pixel) is TPS
    evaluated at one of its own control points -- which a true TPS reproduces
    to numerical precision.
    """
    raw = json.loads(FIXTURE.read_text())
    gcp_crs = CRS.from_string(raw["gcp_crs"])
    gcps = [
        GroundControlPoint(row=g["row"], col=g["col"], x=g["x"], y=g["y"], z=0)
        for g in raw["gcps"]
    ]

    # A subset is enough to characterise the residual and keeps the test fast
    # (each probe rebuilds the TPS solver over the full GCP set).
    probes = gcps[::7]
    assert len(probes) > 20, "expected a representative sample of GCPs"

    row_err_px = []
    col_err_px = []
    eps = 1e-4  # degrees; tiny enough that the 1x1 grid is effectively a point
    for g in probes:
        bounds = (g.x - eps / 2, g.y - eps / 2, g.x + eps / 2, g.y + eps / 2)
        inverse = build_inverse_map(
            gcps, gcp_crs, width=1, height=1, bounds=bounds, dst_crs=gcp_crs
        )
        row_err_px.append(inverse.line[0, 0] - g.row)
        col_err_px.append(inverse.pixel[0, 0] - g.col)

    row_err_px = np.array(row_err_px)
    col_err_px = np.array(col_err_px)

    # Local ground sampling distance (metres/pixel), estimated from two
    # adjacent GCPs on the same range line via a planar Web Mercator
    # approximation (fine at this scale), to convert the pixel residual above
    # into a ground distance without hard-coding Sentinel-1's spec'd pixel
    # spacing.
    same_line = [g for g in raw["gcps"] if g["row"] == raw["gcps"][0]["row"]][:2]
    lons = [g["x"] for g in same_line]
    lats = [g["y"] for g in same_line]
    col_step = same_line[1]["col"] - same_line[0]["col"]
    xs, ys = warp_transform(gcp_crs, CRS.from_epsg(3857), lons, lats)
    ground_step_m = float(np.hypot(xs[1] - xs[0], ys[1] - ys[0]))
    metres_per_pixel = ground_step_m / col_step

    residual_m = np.hypot(row_err_px, col_err_px) * metres_per_pixel
    rms_m = float(np.sqrt((residual_m**2).mean()))
    assert rms_m < 1.0, f"RMS residual {rms_m:.4f} m (>= 1 m)"
