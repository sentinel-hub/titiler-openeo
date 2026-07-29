"""Tests for GCPReader -- georeferencing datasets from their actual GCPs.

rio-tiler's ``Reader`` collapses a dataset's GCP grid to a single affine via
``transform.from_gcps()``. ``GCPReader`` hands GDAL the real GCPs instead. See
docs/adr/0001-sar-backscatter.md S1.6i, issue #343, and the upstream discussion
at cogeotiff/rio-tiler#977.

**The fixture is deliberately polar.** The affine approximation's error is
strongly latitude-dependent -- measured on real products, the two warp paths
diverge by <= 30 m at 69 deg N but by 204-2042 m at 81-86 deg N, because
meridian convergence makes a single affine a poor model of the grid. A
mid-latitude fixture therefore *cannot* discriminate the two paths: their
difference falls below the nearest-resampling quantisation floor. Anyone
retargeting these tests at a temperate scene will find them silently
non-discriminating.

Position is asserted by decoding it from pixel *values*: the raster is a ramp
where every pixel is unique and decodes back to its own (row, col). Three
measurement traps are worth knowing (all cost real time, all are recorded in
the ADR):

* Image correlation cannot validate this geometry -- a real scene's
  across-swath brightness gradient makes even a 200 px offset correlate ~0.9.
* Unnormalised order-3 polynomial fits are ill-conditioned at realistic pixel
  magnitudes and diverge.
* Converting a *rotated* affine's pixel error to metres via |a|/|e| ignores
  rotation; apply the transform forward and compare in ground units instead.
"""

import json
from pathlib import Path

import numpy as np
import pytest
import rasterio
from rasterio.control import GroundControlPoint
from rasterio.crs import CRS
from rasterio.enums import ColorInterp
from rasterio.io import MemoryFile
from rasterio.transform import from_gcps
from rasterio.vrt import WarpedVRT

from titiler.openeo.reader import GCPReader

FIXTURE = Path(__file__).parent / "fixtures" / "sar" / "gcps_ew_grdm_polar.json"

# The real product is 10725 x 10777. Scale it to something a test can hold while
# keeping the grid's shape: lon/lat are untouched, so only row/col shrink.
# Do not scale much harder -- shrinking row/col shrinks the *pixel* error
# proportionally, and the affine-vs-order-3 separation collapses into the
# resampling quantisation floor (measured: 5.9x separation at 4, 2.7x at 8,
# 1.9x at 16).
SCALE = 4


@pytest.fixture(scope="module")
def polar_gcps():
    """The real 81-86 deg N geolocation grid, scaled to a testable raster."""
    raw = json.loads(FIXTURE.read_text())
    height, width = (d // SCALE for d in raw["source_shape"])
    gcps = [
        GroundControlPoint(
            row=g["row"] / SCALE, col=g["col"] / SCALE, x=g["x"], y=g["y"], z=0
        )
        for g in raw["gcps"]
    ]
    gcps = [g for g in gcps if 0 <= g.row < height and 0 <= g.col < width]
    return gcps, CRS.from_string(raw["gcp_crs"]), width, height


@pytest.fixture(scope="module")
def gcp_dataset(polar_gcps):
    """A GCP-referenced raster whose pixel values encode their own (row, col)."""
    gcps, crs, width, height = polar_gcps
    rows, cols = np.mgrid[0:height, 0:width]
    data = (rows * width + cols + 1).astype("uint32")  # unique, non-zero

    with MemoryFile() as memfile:
        with memfile.open(
            driver="GTiff", width=width, height=height, count=1, dtype="uint32"
        ) as dst:
            dst.write(data, 1)
            dst.gcps = (gcps, crs)
        with memfile.open() as src:
            yield src


def _position_error_px(dataset, gcps, width) -> np.ndarray:
    """Per-GCP position error in source pixels, decoded from pixel values."""
    errors = []
    for g in gcps:
        row, col = dataset.index(g.x, g.y)
        if not (0 <= row < dataset.height and 0 <= col < dataset.width):
            continue
        value = int(
            dataset.read(1, window=rasterio.windows.Window(col, row, 1, 1))[0, 0]
        )
        if value == 0:
            continue  # nodata / outside the warped footprint
        got_row, got_col = divmod(value - 1, width)
        errors.append(np.hypot(got_row - g.row, got_col - g.col))
    return np.array(errors)


def test_gcp_reader_beats_the_collapsed_affine(gcp_dataset, polar_gcps):
    """The real-GCP warp is materially better than rio-tiler's from_gcps affine.

    This is the regression guard for the defect itself. It asserts the
    *improvement* rather than that a particular option was passed, so it keeps
    working if the mechanism changes -- notably once the upstream fix in
    cogeotiff/rio-tiler#977 lands and this subclass can be dropped.
    """
    gcps, _, width, _ = polar_gcps

    with GCPReader(None, dataset=gcp_dataset) as reader:
        fixed = _position_error_px(reader.dataset, gcps, width)

    with WarpedVRT(
        gcp_dataset,
        src_crs=gcp_dataset.gcps[1],
        src_transform=from_gcps(gcp_dataset.gcps[0]),
    ) as vrt:
        collapsed = _position_error_px(vrt, gcps, width)

    assert len(fixed) > 100, "expected most GCPs to be probed"
    # Measured ~5.9x on max and ~3.2x on RMS; assert well inside that so the
    # test is not brittle to GDAL version differences in transformer selection.
    assert (
        collapsed.max() > 2.0 * fixed.max()
    ), f"affine max={collapsed.max():.2f} px vs gcp max={fixed.max():.2f} px"
    assert np.sqrt((collapsed**2).mean()) > 2.0 * np.sqrt((fixed**2).mean()), (
        f"affine rms={np.sqrt((collapsed**2).mean()):.2f} px vs "
        f"gcp rms={np.sqrt((fixed**2).mean()):.2f} px"
    )


def test_gcp_reader_accuracy_on_a_real_polar_grid(gcp_dataset, polar_gcps):
    """GCPReader keeps position error small on a genuinely hard grid.

    Not sub-pixel: at 81-86 deg N even an order-3 fit leaves a few pixels of
    residual, and the value-decode probe carries its own resampling
    quantisation. The bound below is a regression guard, not a precision claim.
    """
    gcps, _, width, _ = polar_gcps
    with GCPReader(None, dataset=gcp_dataset) as reader:
        err = _position_error_px(reader.dataset, gcps, width)

    assert np.sqrt((err**2).mean()) < 4.0, f"RMS {np.sqrt((err**2).mean()):.2f} px"
    assert err.max() < 10.0, f"max {err.max():.2f} px"


def test_gcp_reader_georeferences_from_gcps(gcp_dataset):
    """A GCP dataset comes back georeferenced, with the GCPs consumed."""
    with GCPReader(None, dataset=gcp_dataset) as reader:
        assert reader.crs == CRS.from_epsg(4326)
        assert reader.dataset.gcps[0] == []  # consumed by the warp
        left, bottom, right, top = reader.bounds
        assert left < right and bottom < top


def test_non_gcp_dataset_is_untouched():
    """Datasets without GCPs behave exactly as before -- no VRT, no rewrite."""
    transform = rasterio.transform.from_bounds(0, 0, 10, 10, 8, 8)
    with MemoryFile() as memfile:
        with memfile.open(
            driver="GTiff",
            width=8,
            height=8,
            count=1,
            dtype="uint8",
            crs=CRS.from_epsg(4326),
            transform=transform,
        ) as dst:
            dst.write(np.arange(64, dtype="uint8").reshape(8, 8), 1)
        with memfile.open() as src:
            with GCPReader(None, dataset=src) as reader:
                assert reader.dataset is src  # not wrapped
                assert reader.crs == CRS.from_epsg(4326)
                assert reader.bounds == (0.0, 0.0, 10.0, 10.0)


def test_simplestacreader_uses_gcp_reader_by_default():
    """SimpleSTACReader routes assets through GCPReader."""
    import attr

    from titiler.openeo.reader import SimpleSTACReader

    default = next(
        f.default for f in attr.fields(SimpleSTACReader) if f.name == "reader"
    )
    assert default is GCPReader


def _write_gcp_tif(path, polar_gcps, *, nodata=None, alpha=False):
    """Write a small GCP-referenced GeoTIFF to disk."""
    gcps, crs, width, height = polar_gcps
    width, height = min(width, 64), min(height, 64)
    scaled = [
        GroundControlPoint(
            row=g.row * height / 673, col=g.col * width / 670, x=g.x, y=g.y, z=0
        )
        for g in gcps
    ]
    count = 2 if alpha else 1
    profile = {
        "driver": "GTiff",
        "width": width,
        "height": height,
        "count": count,
        "dtype": "uint8",
    }
    if nodata is not None:
        profile["nodata"] = nodata
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(np.full((height, width), 7, "uint8"), 1)
        if alpha:
            dst.write(np.full((height, width), 255, "uint8"), 2)
            dst.colorinterp = [ColorInterp.gray, ColorInterp.alpha]
        dst.gcps = (scaled, crs)
    return path


def test_gcp_reader_opens_from_a_path(tmp_path, polar_gcps):
    """The input-path branch works, not just a pre-opened dataset."""
    path = _write_gcp_tif(tmp_path / "gcp.tif", polar_gcps)
    with GCPReader(str(path)) as reader:
        assert reader.crs == CRS.from_epsg(4326)
        assert reader.dataset.gcps[0] == []  # warped, GCPs consumed


def test_gcp_reader_preserves_nodata_instead_of_adding_alpha(tmp_path, polar_gcps):
    """A source nodata value is carried into the VRT rather than an alpha band.

    Real Sentinel-1 GRD assets declare nodata=0, so this is the branch they
    actually take -- worth covering explicitly rather than only exercising
    fixtures that happen to have no nodata.
    """
    path = _write_gcp_tif(tmp_path / "nodata.tif", polar_gcps, nodata=0)
    with GCPReader(str(path)) as reader:
        assert reader.dataset.nodata == 0
        assert reader.dataset.count == 1  # no alpha band added


def test_gcp_reader_does_not_double_up_an_existing_alpha_band(tmp_path, polar_gcps):
    """A source that already has an alpha band does not get a second one."""
    path = _write_gcp_tif(tmp_path / "alpha.tif", polar_gcps, alpha=True)
    with GCPReader(str(path)) as reader:
        assert reader.dataset.count == 2  # unchanged, not 3
