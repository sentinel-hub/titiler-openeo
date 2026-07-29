"""Tests for GCPReader -- georeferencing datasets from their actual GCPs.

The geometry fixture uses a **real** Sentinel-1 IW GRDH geolocation grid
(`fixtures/sar/gcps_iw_grdh.json`, 189 GCPs), scaled down to a testable raster
size. Row/col are divided by a constant; longitude/latitude are untouched, so
the grid's curvature in normalised coordinates -- the thing that defeats an
affine fit -- is preserved exactly. An invented curve would only test an
invented regime.

Position is asserted by decoding it from pixel *values*: the fixture is a ramp
where every pixel is unique and decodes back to its own (row, col). Two traps
made image-based checks useless during investigation (ADR 0001 S1.6i):

* Correlation cannot validate this. A real scene has an across-swath brightness
  gradient, so even a 200 px misalignment still correlates ~0.9.
* An unnormalised order-3 polynomial fit is ill-conditioned at realistic pixel
  magnitudes and diverges.
"""

import json
from pathlib import Path

import numpy as np
import pytest
import rasterio
from rasterio.control import GroundControlPoint
from rasterio.crs import CRS
from rasterio.io import MemoryFile
from rasterio.transform import from_gcps
from rasterio.vrt import WarpedVRT

from titiler.openeo.reader import GCPReader

FIXTURE = Path(__file__).parent / "fixtures" / "sar" / "gcps_iw_grdh.json"

# The real product is 26545 x 15940; scale it to something a test can hold while
# keeping the grid's shape. 16 -> 1659 x 996. Do not scale much harder: longitude
# and latitude are untouched, so shrinking row/col also shrinks the *pixel* error
# proportionally, and past ~SCALE 40 the affine's error drops into the
# nearest-resampling quantisation floor and the comparison stops discriminating.
SCALE = 16


@pytest.fixture(scope="module")
def real_gcps():
    """The real S1 IW GRDH geolocation grid, scaled to a testable raster."""
    raw = json.loads(FIXTURE.read_text())
    height, width = (d // SCALE for d in raw["source_shape"])
    gcps = [
        GroundControlPoint(
            row=g["row"] / SCALE, col=g["col"] / SCALE, x=g["x"], y=g["y"], z=0
        )
        for g in raw["gcps"]
    ]
    # keep only GCPs that land inside the scaled raster
    gcps = [g for g in gcps if 0 <= g.row < height and 0 <= g.col < width]
    return gcps, CRS.from_string(raw["gcp_crs"]), width, height


@pytest.fixture
def gcp_dataset(real_gcps):
    """A GCP-referenced raster whose pixel values encode their own (row, col)."""
    gcps, crs, width, height = real_gcps
    rows, cols = np.mgrid[0:height, 0:width]
    # unique per pixel and non-zero, so 0 can mean nodata
    data = (rows * width + cols + 1).astype("uint32")

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
        r, c = dataset.index(g.x, g.y)
        if not (0 <= r < dataset.height and 0 <= c < dataset.width):
            continue
        value = int(dataset.read(1, window=rasterio.windows.Window(c, r, 1, 1))[0, 0])
        if value == 0:
            continue  # nodata / outside the warped footprint
        got_row, got_col = divmod(value - 1, width)
        errors.append(np.hypot(got_row - g.row, got_col - g.col))
    return np.array(errors)


def test_gcp_reader_is_accurate_on_a_real_grid(gcp_dataset, real_gcps):
    """GCPReader reproduces a real SAR geolocation grid to ~a pixel."""
    gcps, _, width, _ = real_gcps
    with GCPReader(None, dataset=gcp_dataset) as reader:
        err = _position_error_px(reader.dataset, gcps, width)

    assert len(err) > 50, "expected most GCPs to be probed"
    assert err.max() <= 2.0, f"max position error {err.max():.2f} px"
    assert np.sqrt((err**2).mean()) <= 1.0, f"RMS {np.sqrt((err**2).mean()):.2f} px"


def test_gcp_reader_beats_the_collapsed_affine(gcp_dataset, real_gcps):
    """The real-GCP warp is materially better than rio-tiler's from_gcps affine.

    Regression guard for the defect itself: rio-tiler passes
    ``src_transform=from_gcps(...)``, collapsing the grid to one affine. This
    asserts the *improvement*, not that a particular option was passed, so it
    still holds if the mechanism changes -- e.g. once the upstream fix in
    cogeotiff/rio-tiler#977 lands and this subclass is dropped.
    """
    gcps, _, width, _ = real_gcps

    with GCPReader(None, dataset=gcp_dataset) as reader:
        fixed = _position_error_px(reader.dataset, gcps, width)

    with WarpedVRT(
        gcp_dataset,
        src_crs=gcp_dataset.gcps[1],
        src_transform=from_gcps(gcp_dataset.gcps[0]),
    ) as vrt:
        collapsed = _position_error_px(vrt, gcps, width)

    assert collapsed.max() > 5 * fixed.max(), (
        f"expected the collapsed affine to be much worse; "
        f"affine max={collapsed.max():.2f} px, gcp max={fixed.max():.2f} px"
    )


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
