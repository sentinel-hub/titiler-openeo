"""Readers that compute a band's values on the destination grid, from a
non-raster STAC asset, rather than reading pixels from a raster asset.

These are the per-asset readers `SimpleSTACReader` dispatches a derived
(pseudo-asset) band to, through the same `_get_asset_info`/`_get_reader` hooks
a real raster asset goes through -- so they compose with `multi_arrays` and
mosaicking for free (docs/adr/0002-band-sources.md S2.3).

Every reader in this module ties to Sentinel-1 GRD's GCP-referenced geometry
(`sar.geocode.build_inverse_map`, over the measurement asset's own GCPs, never
item/asset `proj:*` -- issue #338). Sentinel-2's view/sun angle bands
(docs/adr/0004-sentinel2-view-sun-angle-bands.md) are the non-GCP band source
this module's own docstring once anticipated needing its own base for -- see
`sentinel2_readers.py`'s `Sentinel2AngleReader`, not a subclass of
`BandReader` below.
"""

from contextlib import nullcontext
from threading import Lock
from typing import Any, Dict, Optional, Tuple

import attr
import numpy
from rasterio.crs import CRS
from rasterio.warp import transform_bounds
from rio_tiler.constants import WGS84_CRS
from rio_tiler.io.base import BaseReader
from rio_tiler.models import ImageData
from rio_tiler.types import BBox

from ..sar import annotation, geocode
from ..sar.fetcher import AssetFetcher
from ..sar.geocode import InverseMap

__all__ = ["BandReader", "CalibrationBandReader", "NoiseBandReader"]

#: Keys a memoized inverse map by exactly the inputs that determine it.
_InverseMapKey = Tuple[str, Tuple[float, float, float, float], int, int, str]


@attr.s
class BandReader(BaseReader):
    """Base for a derived band computed on the destination grid via GCPs.

    `input` is the annotation asset's href -- what a subclass's `_evaluate`
    fetches and parses (e.g. `annotation.get_noise`). `sibling_href` is the
    measurement asset's href -- what `geocode.get_gcps` opens, header-only,
    for this item's GCPs. `fetcher` is an optional `AssetFetcher` override,
    mirroring `sar_backscatter`'s `options["fetcher"]` seam one level down;
    `None` (the default in production) falls back to
    `sar.fetcher.get_default_fetcher()` inside `annotation.get_*`. `quantity`
    is opaque here -- passed straight to a subclass's `_evaluate` so one
    asset can back several distinctly-computed bands
    (`CalibrationBandReader`'s five, sharing one calibration annotation).

    `inverse_map_cache`, if given, is a plain dict this instance reads from
    and writes to, keyed on the exact inputs that determine
    `geocode.build_inverse_map`'s result. `SimpleSTACReader` hands every
    derived-band reader constructed for one `part()` call **the same** dict
    (ADR 0002 S2.5/increment-3 risk log): those reads share one destination
    grid and one measurement asset's GCPs by construction, so the fit is
    identical and only needs computing once, however many bands from that
    asset are requested together. Scoped to one item-read (the dict dies with
    the `SimpleSTACReader` instance that created it) -- deliberately not a
    module-global cache, which would have no natural eviction and no
    correctly-sized key space (unlike `annotation`/`geocode`'s own
    href-keyed caches, whose key space is bounded by distinct assets, this
    would be keyed additionally by bbox/width/height/dst_crs, i.e.
    effectively per-request).

    `inverse_map_lock` (required whenever `inverse_map_cache` is given) is
    the same `threading.Lock` shared the same way. `RasterStack` reads
    per-asset via a thread pool (`multi_arrays`/`create_tasks`), so several
    of these readers' `part()` calls run concurrently -- without a lock,
    multiple threads can all miss the cache before any of them stores a
    result (confirmed: 5 concurrent requests for one asset produced 2 builds,
    not 1, before this was added), the same race
    `annotation.py`/`geocode.py`'s own `condition=` caches exist to prevent.
    A single coarse lock (not per-key) is enough here: the whole cache is
    scoped to one `part()` call, effectively one entry.

    Only `part()` has a real implementation: it is the only method
    `SimpleSTACReader`'s production read path
    (`reader._reader` -> `SimpleSTACReader.part()`) ever calls for a
    band-source asset today. `BaseReader`'s other abstractions
    (`tile`/`preview`/`point`/`feature`/`info`/`statistics`) exist only
    because `rio_tiler.io.base.BaseReader` must be fully implemented to be
    instantiated at all; none is reachable from any current titiler-openeo
    endpoint for these assets, so each raises rather than guessing at a
    meaning that would go untested.
    """

    sibling_href: str = attr.ib(kw_only=True)
    fetcher: Optional[AssetFetcher] = attr.ib(default=None, kw_only=True)
    quantity: Optional[str] = attr.ib(default=None, kw_only=True)
    inverse_map_cache: Optional[Dict[_InverseMapKey, InverseMap]] = attr.ib(
        default=None, kw_only=True
    )
    inverse_map_lock: Optional[Lock] = attr.ib(default=None, kw_only=True)

    def __attrs_post_init__(self) -> None:
        """No fixed grid of its own -- every part() call defines its own
        destination grid from the caller's bbox/width/height/dst_crs."""
        self.bounds = (-180.0, -90.0, 180.0, 90.0)
        self.crs = WGS84_CRS
        self.transform = None
        self.height = None
        self.width = None

    def part(
        self,
        bbox: BBox,
        dst_crs: Optional[CRS] = None,
        bounds_crs: CRS = WGS84_CRS,
        width: Optional[int] = None,
        height: Optional[int] = None,
        buffer: Optional[float] = None,
        **kwargs: Any,
    ) -> ImageData:
        """Evaluate this band at every destination pixel centre.

        Mirrors the (bbox, dst_crs, bounds_crs, width, height, buffer)
        contract `rio_tiler.io.rasterio.Reader.part` accepts, since
        `MultiBaseReader.part`'s per-asset closure calls every asset's reader
        with the same kwargs regardless of whether it is real or derived.
        """
        dst_crs = dst_crs or bounds_crs
        if bounds_crs and CRS.from_user_input(bounds_crs) != CRS.from_user_input(
            dst_crs
        ):
            bbox = transform_bounds(bounds_crs, dst_crs, *bbox, densify_pts=21)

        width = width or 256
        height = height or 256

        if buffer:
            # Mirror rio-tiler's ground-relative buffer: expand the grid by
            # `buffer` pixels on each edge before computing pixel centres,
            # rather than cropping after the fact.
            px_size = (bbox[2] - bbox[0]) / width
            py_size = (bbox[3] - bbox[1]) / height
            bbox = (
                bbox[0] - buffer * px_size,
                bbox[1] - buffer * py_size,
                bbox[2] + buffer * px_size,
                bbox[3] + buffer * py_size,
            )
            width += 2 * round(buffer)
            height += 2 * round(buffer)

        inverse = self._get_inverse_map(bbox, width, height, dst_crs)

        values = self._evaluate(inverse.line, inverse.pixel).astype("float32")
        array = numpy.ma.MaskedArray(
            values[numpy.newaxis, :, :],
            mask=numpy.zeros((1, height, width), dtype="bool"),
        )
        return ImageData(array, crs=dst_crs, bounds=tuple(bbox))

    def _get_inverse_map(
        self, bbox: BBox, width: int, height: int, dst_crs: CRS
    ) -> InverseMap:
        """`geocode.build_inverse_map`, memoized per `inverse_map_cache` if given.

        Holds `inverse_map_lock` for the whole check-then-build-then-store
        section, not just the dict access: `RasterStack` reads assets via a
        thread pool, so several of these readers' `part()` calls can run
        concurrently, and a plain `dict.get`-then-`dict.__setitem__` leaves a
        window where multiple threads all miss the cache before any of them
        stores a result -- the same single-flight problem
        `annotation.py`/`geocode.py`'s own `condition=` caches solve, just
        with a plain lock here since the cache is small enough (effectively
        one entry) that per-key granularity buys nothing.
        """
        if self.inverse_map_cache is None:
            gcps, gcp_crs = geocode.get_gcps(self.sibling_href)
            return geocode.build_inverse_map(
                gcps, gcp_crs, width, height, bbox, dst_crs
            )

        key: _InverseMapKey = (
            self.sibling_href,
            tuple(bbox),
            width,
            height,
            dst_crs.to_string(),
        )
        lock = self.inverse_map_lock or nullcontext()
        with lock:
            cached = self.inverse_map_cache.get(key)
            if cached is not None:
                return cached

            gcps, gcp_crs = geocode.get_gcps(self.sibling_href)
            inverse = geocode.build_inverse_map(
                gcps, gcp_crs, width, height, bbox, dst_crs
            )
            self.inverse_map_cache[key] = inverse
            return inverse

    def _evaluate(self, line: numpy.ndarray, pixel: numpy.ndarray) -> numpy.ndarray:
        """Return this band's value at each source ``(line, pixel)`` coordinate."""
        raise NotImplementedError

    def _unreachable(self, name: str) -> Any:
        raise NotImplementedError(
            f"{type(self).__name__}.{name}() is not implemented -- band-source "
            "assets are only ever read via part(), see class docstring."
        )

    def info(self, *args: Any, **kwargs: Any) -> Any:
        """Not implemented -- see class docstring."""
        return self._unreachable("info")

    def statistics(self, *args: Any, **kwargs: Any) -> Any:
        """Not implemented -- see class docstring."""
        return self._unreachable("statistics")

    def tile(self, *args: Any, **kwargs: Any) -> Any:
        """Not implemented -- see class docstring."""
        return self._unreachable("tile")

    def point(self, *args: Any, **kwargs: Any) -> Any:
        """Not implemented -- see class docstring."""
        return self._unreachable("point")

    def feature(self, *args: Any, **kwargs: Any) -> Any:
        """Not implemented -- see class docstring."""
        return self._unreachable("feature")

    def preview(self, *args: Any, **kwargs: Any) -> Any:
        """Not implemented -- see class docstring."""
        return self._unreachable("preview")


@attr.s
class NoiseBandReader(BandReader):
    """Sentinel-1 GRD thermal-noise LUT (`<pol>_noise_lut`), in DN^2."""

    def _evaluate(self, line: numpy.ndarray, pixel: numpy.ndarray) -> numpy.ndarray:
        return annotation.get_noise(self.input, fetcher=self.fetcher).evaluate(
            line, pixel
        )


@attr.s
class CalibrationBandReader(BandReader):
    """Sentinel-1 GRD calibration LUTs and the derived incidence angle.

    One calibration annotation backs five distinct bands
    (`<pol>_sigma0_lut`/`_beta0_lut`/`_gamma0_lut`/`_dn_lut`/
    `_ellipsoid_incidence_angle`); `quantity` -- set per band name by the
    registry entry in `sources.py` -- names the `annotation.CalibrationLUT`
    method to call, so this one class serves all five rather than needing a
    subclass per quantity.
    """

    def _evaluate(self, line: numpy.ndarray, pixel: numpy.ndarray) -> numpy.ndarray:
        if self.quantity is None:
            raise ValueError(
                f"{type(self).__name__} requires a `quantity` "
                "(set by the matching BandSource.bands entry in sources.py)"
            )
        calibration = annotation.get_calibration(self.input, fetcher=self.fetcher)
        return getattr(calibration, self.quantity)(line, pixel)
