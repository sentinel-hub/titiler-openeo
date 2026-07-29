"""Geocoding for Sentinel-1 GRD: destination grid -> source (line, pixel).

Builds the inverse map from destination pixel centres to source (line,
pixel) coordinates using a thin-plate spline over the measurement TIFF's
ground control points. That map is what calibration.py evaluates the
calibration/noise LUTs at -- it does not read pixels.

**Reading is `RasterStack`'s job, not this module's** (docs/adr/0001-sar-backscatter.md
S7.3, S7.10). Duplicating a windowed, decimated, resampled read here would fork
overviews, CRS handling, masks, mosaicking, output-size limits and retries away from
rio-tiler, and would inevitably drift from it. The DN pixels are read through the
normal `load_collection` path (`OpenEOReader` already warps GCP-referenced datasets from
their real GCPs -- issue #344), and this module only tells the caller where, in the
source product, each destination pixel's radiometry LUTs live.

`rasterio.warp.reproject` is deliberately not used here: it silently ignores
`METHOD=GCP_TPS` and always runs an order-2 polynomial, which on IW GRDH is a
~25 m RMS geolocation error that looks like it worked
(docs/adr/0001-sar-backscatter.md S1.6b).
"""

from dataclasses import dataclass
from typing import Sequence

import numpy as np
from rasterio.control import GroundControlPoint
from rasterio.crs import CRS
from rasterio.transform import GCPTransformer, from_bounds, xy
from rasterio.warp import transform as warp_transform
from rio_tiler.types import BBox

__all__ = ["InverseMap", "build_inverse_map"]


@dataclass(frozen=True)
class InverseMap:
    """Maps destination pixel centres back to source (line, pixel).

    Both arrays have shape (height, width) and hold fractional (sub-pixel)
    coordinates, never rounded, since calibration.py bilinearly interpolates
    the LUTs at them.
    """

    line: np.ndarray
    pixel: np.ndarray


def build_inverse_map(
    gcps: Sequence[GroundControlPoint],
    gcp_crs: CRS,
    width: int,
    height: int,
    bounds: BBox,
    dst_crs: CRS,
) -> InverseMap:
    """Build the destination-to-source inverse map, once.

    `bounds`/`dst_crs`/`width`/`height` describe the destination grid, exactly as
    `RasterStack` already carries them for the polarisation's `ImageData`. `gcps`/
    `gcp_crs` must come from the measurement TIFF itself (`src.gcps`, a header-only
    open), never from item/asset `proj:*` -- some catalogues advertise a bbox-derived
    affine that is fiction for SAR geometry (ADR S1.7).

    Destination pixel centres are computed in `dst_crs` (typically Web Mercator for a
    tile server) and reprojected into `gcp_crs` (typically EPSG:4326) before the GCP
    transform, since `GCPTransformer` expects coordinates in the GCPs' own CRS.
    """
    dst_transform = from_bounds(*bounds, width, height)
    rows, cols = np.mgrid[0:height, 0:width]
    xs, ys = xy(dst_transform, rows.ravel(), cols.ravel())
    xs = np.asarray(xs, dtype="f8")
    ys = np.asarray(ys, dtype="f8")

    if dst_crs != gcp_crs:
        xs, ys = warp_transform(dst_crs, gcp_crs, xs, ys)
        xs = np.asarray(xs, dtype="f8")
        ys = np.asarray(ys, dtype="f8")

    with GCPTransformer(gcps, tps=True) as transformer:
        # op=lambda v: v keeps fractional coordinates; the default (floor)
        # would throw away exactly the sub-pixel precision bilinear LUT
        # interpolation needs.
        line, pixel = transformer.rowcol(xs, ys, op=lambda v: v)

    return InverseMap(
        line=np.asarray(line, dtype="f8").reshape(height, width),
        pixel=np.asarray(pixel, dtype="f8").reshape(height, width),
    )
