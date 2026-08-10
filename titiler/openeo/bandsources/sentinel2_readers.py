"""Readers for Sentinel-2 view/sun angle bands (docs/adr/0004-sentinel2-view-sun-angle-bands.md).

Unlike `readers.py`'s `BandReader` (Sentinel-1, GCP-referenced), Sentinel-2
imagery has ordinary affine georeferencing -- no ground-control-point
inverse mapping is needed, only a CRS reprojection of the destination grid
into the tile's own CRS (`Tile_Geocoding`, from `MTD_TL.xml`). This is a
separate reader base, not a `BandReader` subclass: that base is documented
and shaped around GCPs and mandates a `sibling_href` this reader does not
use for georeferencing.
"""

from typing import Any, Optional

import attr
import numpy
from rasterio.crs import CRS
from rasterio.transform import from_bounds, xy
from rasterio.warp import transform as warp_transform
from rasterio.warp import transform_bounds
from rio_tiler.constants import WGS84_CRS
from rio_tiler.io.base import BaseReader
from rio_tiler.models import ImageData
from rio_tiler.types import BBox

from ..sar.fetcher import AssetFetcher
from ..sentinel2.tile_metadata import TileMetadata, get_tile_metadata

__all__ = ["Sentinel2AngleReader", "ViewAngleMeanReader", "SunAngleGridReader"]


@attr.s
class Sentinel2AngleReader(BaseReader):
    """Base for a band computed from Sentinel-2's `MTD_TL.xml` tile metadata.

    `input` is the `granule_metadata`/`granule-metadata` asset's href.
    `sibling_href`/`inverse_map_cache`/`inverse_map_lock` are accepted and
    ignored: `reader.py`'s `_get_derived_asset_info` threads them through
    unconditionally for any derived band with a resolved sibling (mirroring
    `BandReader`'s attrs shape), and this reader needs none of them -- its
    own `part()` call already carries the exact destination grid
    (bbox/width/height/dst_crs), and its geometry comes from the asset's own
    `Tile_Geocoding`, not from another asset's pixels.
    """

    fetcher: Optional[AssetFetcher] = attr.ib(default=None, kw_only=True)
    #: "zenith" | "azimuth" -- which TileAngles quantity this band is.
    quantity: Optional[str] = attr.ib(default=None, kw_only=True)
    sibling_href: Optional[str] = attr.ib(default=None, kw_only=True)
    inverse_map_cache: Optional[Any] = attr.ib(default=None, kw_only=True)
    inverse_map_lock: Optional[Any] = attr.ib(default=None, kw_only=True)

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
        contract `BandReader.part` (readers.py) accepts, since
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

        metadata = get_tile_metadata(self.input, fetcher=self.fetcher)
        values = self._evaluate(metadata, height, width, bbox, dst_crs).astype(
            "float32"
        )
        array = numpy.ma.MaskedArray(
            values[numpy.newaxis, :, :],
            mask=numpy.zeros((1, height, width), dtype="bool"),
        )
        return ImageData(array, crs=dst_crs, bounds=tuple(bbox))

    def _evaluate(
        self,
        metadata: TileMetadata,
        height: int,
        width: int,
        bbox: BBox,
        dst_crs: CRS,
    ) -> numpy.ndarray:
        """Return this band's (height, width) values for the destination grid."""
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
class ViewAngleMeanReader(Sentinel2AngleReader):
    """`viewZenithMean`/`viewAzimuthMean`: the mean-across-bands scalar from
    `Mean_Viewing_Incidence_Angle_List`, broadcast uniformly -- no finer
    spatial signal is available at this granularity (ADR 0004 S1.3)."""

    def _evaluate(self, metadata, height, width, bbox, dst_crs) -> numpy.ndarray:
        if self.quantity == "zenith":
            value = metadata.angles.mean_view_zenith
        elif self.quantity == "azimuth":
            value = metadata.angles.mean_view_azimuth
        else:
            raise ValueError(
                f"{type(self).__name__} requires quantity 'zenith' or "
                f"'azimuth' (set by the matching BandSource.bands entry in "
                f"sentinel2_sources.py), got {self.quantity!r}"
            )
        return numpy.full((height, width), value, dtype="f8")


@attr.s
class SunAngleGridReader(Sentinel2AngleReader):
    """`sunZenithAngles`/`sunAzimuthAngles`: bilinearly interpolated from the
    real 23x23 `Sun_Angles_Grid`, at each destination pixel centre
    reprojected into the tile's own CRS -- ordinary affine math, no GCPs."""

    def _evaluate(self, metadata, height, width, bbox, dst_crs) -> numpy.ndarray:
        if self.quantity not in ("zenith", "azimuth"):
            raise ValueError(
                f"{type(self).__name__} requires quantity 'zenith' or "
                f"'azimuth' (set by the matching BandSource.bands entry in "
                f"sentinel2_sources.py), got {self.quantity!r}"
            )

        dst_transform = from_bounds(*bbox, width, height)
        rows, cols = numpy.mgrid[0:height, 0:width]
        xs, ys = xy(dst_transform, rows.ravel(), cols.ravel())
        xs = numpy.asarray(xs, dtype="f8")
        ys = numpy.asarray(ys, dtype="f8")

        tile_crs = CRS.from_user_input(metadata.geocoding.crs)
        if CRS.from_user_input(dst_crs) != tile_crs:
            xs, ys = warp_transform(dst_crs, tile_crs, xs, ys)
            xs = numpy.asarray(xs, dtype="f8")
            ys = numpy.asarray(ys, dtype="f8")

        # Grid units: (0, 0) is the tile's own (ULX, ULY) corner, ascending
        # in the same direction Grid2D.interp expects (its own
        # np.searchsorted assumes ascending lines/pixels) -- row increases
        # southward (as Y decreases), matching the grid's own row order.
        row_units = (metadata.geocoding.uly - ys).reshape(height, width)
        col_units = (xs - metadata.geocoding.ulx).reshape(height, width)

        return metadata.angles.sun_grid.interp(self.quantity, row_units, col_units)
