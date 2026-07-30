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
from threading import Condition
from typing import Sequence, Tuple
from urllib.parse import urlparse

import numpy as np
import rasterio
from cachetools import LRUCache, cached
from cachetools.keys import hashkey
from rasterio.control import GroundControlPoint
from rasterio.crs import CRS
from rasterio.transform import GCPTransformer, from_bounds, xy
from rasterio.warp import transform as warp_transform
from rio_tiler.types import BBox

from ..settings import SARSettings

__all__ = ["InverseMap", "build_inverse_map", "get_gcps"]

_settings = SARSettings()


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


def _gdal_path(href: str) -> str:
    """Translate a STAC href into something rasterio/GDAL can open."""
    parsed = urlparse(href)
    if parsed.scheme == "s3":
        return f"/vsis3/{parsed.netloc}{parsed.path}"
    if parsed.scheme in ("http", "https"):
        return f"/vsicurl/{href}"
    return href


# Thread-safe cache of parsed GCP sets, keyed on the measurement asset href.
# Mirrors annotation.py's calibration/noise caches: `condition=` makes concurrent
# misses for the same href single-flight (RasterStack executes tasks on a thread
# pool) rather than each triggering its own header read.
_gcp_cache: LRUCache = LRUCache(maxsize=_settings.annotation_cache_maxsize)
_gcp_cache_condition = Condition()


@cached(_gcp_cache, key=lambda href: hashkey(href), condition=_gcp_cache_condition)
def get_gcps(href: str) -> Tuple[Sequence[GroundControlPoint], CRS]:
    """Fetch a measurement asset's GCPs, header-only, cached by href.

    This must open the measurement asset independently of any already-realized
    read: `OpenEOReader` (the GCP-warping reader read through the normal
    `load_collection` path, issue #344) *consumes* the source GCPs while
    building its warped VRT, so by the time a process has that asset's
    `ImageData`, `src.gcps` on that dataset is already empty. Getting the real
    GCPs back means opening the source file again -- but only its header
    (`rasterio.open` does not read pixel data), and only once per href thanks
    to the cache above.
    """
    with rasterio.open(_gdal_path(href)) as src:
        gcps, gcp_crs = src.gcps
        if not gcps:
            raise ValueError(
                f"Measurement asset {href!r} has no GCPs -- unexpected for "
                "Sentinel-1 GRD"
            )
        return gcps, gcp_crs
