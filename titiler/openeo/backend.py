"""titiler-openeo custom Mosaic Backend."""

import json
from typing import Any, Dict, List, Optional, Sequence, Tuple, Type
from urllib.parse import urlparse

import attr
import rasterio
from cachetools import TTLCache, cached
from cachetools.keys import hashkey
from cogeo_mosaic.backends import BaseBackend
from cogeo_mosaic.errors import NoAssetFoundError
from cogeo_mosaic.mosaic import MosaicJSON
from geojson_pydantic import Point, Polygon
from geojson_pydantic.geometries import Geometry, parse_geometry_obj
from morecantile import Tile, TileMatrixSet
from rasterio.crs import CRS
from rasterio.transform import array_bounds
from rasterio.warp import transform, transform_bounds, transform_geom
from rio_tiler.constants import WEB_MERCATOR_TMS, WGS84_CRS
from rio_tiler.errors import InvalidAssetName, MissingAssets
from rio_tiler.io import Reader
from rio_tiler.io.base import BaseReader, MultiBaseReader
from rio_tiler.models import ImageData
from rio_tiler.mosaic import mosaic_reader
from rio_tiler.types import AssetInfo, BBox

from titiler.openeo.settings import CacheSettings

from .stac import STACBackend

cache_config = CacheSettings()


@attr.s
class SimpleSTACReader(MultiBaseReader):
    """Simplified STAC Reader.

    Inputs should be in form of:
    ```json
    {
        "id": "IAMASTACITEM",
        "collection": "mycollection",
        "bbox": (0, 0, 10, 10),
        "assets": {
            "COG": {
                "href": "https://somewhereovertherainbow.io/cog.tif"
            }
        }
    }
    ```

    """

    input: Dict[str, Any] = attr.ib()

    tms: TileMatrixSet = attr.ib(default=WEB_MERCATOR_TMS)
    minzoom: int = attr.ib(default=None)
    maxzoom: int = attr.ib(default=None)

    assets: Sequence[str] = attr.ib(init=False)
    default_assets: Optional[Sequence[str]] = attr.ib(default=None)

    reader: Type[BaseReader] = attr.ib(default=Reader)
    reader_options: Dict = attr.ib(factory=dict)

    ctx: Any = attr.ib(default=rasterio.Env)

    def __attrs_post_init__(self) -> None:
        """Set reader spatial infos and list of valid assets."""
        self.bounds = self.input["bbox"]
        self.crs = WGS84_CRS  # Per specification STAC items are in WGS84

        if proj := self.input.get("proj"):
            crs_string = proj.get("code") or proj.get("epsg") or proj.get("wkt")
            if all(
                [
                    proj.get("transform"),
                    proj.get("shape"),
                    crs_string,
                ]
            ):
                self.height, self.width = proj.get("shape")
                self.transform = proj.get("transform")
                self.bounds = array_bounds(self.height, self.width, self.transform)
                self.crs = rasterio.crs.CRS.from_string(crs_string)

        self.minzoom = self.minzoom if self.minzoom is not None else self._minzoom
        self.maxzoom = self.maxzoom if self.maxzoom is not None else self._maxzoom

        self.assets = list(self.input["assets"])

        if not self.assets:
            raise MissingAssets(
                "No valid asset found. Asset's media types not supported"
            )

    def _parse_vrt_asset(self, asset: str) -> Tuple[str, Optional[str]]:
        if asset.startswith("vrt://") and asset not in self.assets:
            parsed = urlparse(asset)
            if not parsed.netloc:
                raise InvalidAssetName(
                    f"'{asset}' is not valid, couldn't find valid asset"
                )

            if parsed.netloc not in self.assets:
                raise InvalidAssetName(
                    f"'{parsed.netloc}' is not valid, should be one of {self.assets}"
                )

            return parsed.netloc, parsed.query

        return asset, None

    def _get_asset_info(self, asset: str) -> AssetInfo:
        """Validate asset names and return asset's url.

        Args:
            asset (str): STAC asset name.

        Returns:
            str: STAC asset href.

        """
        asset, vrt_options = self._parse_vrt_asset(asset)
        if asset not in self.assets:
            raise InvalidAssetName(
                f"{asset} is not valid. Should be one of {self.assets}"
            )

        asset_info = self.input["assets"][asset]
        info = AssetInfo(
            url=asset_info["href"],
            env={},
        )

        if media_type := asset_info.get("type"):
            info["media_type"] = media_type

        if header_size := asset_info.get("file:header_size"):
            info["env"]["GDAL_INGESTED_BYTES_AT_OPEN"] = header_size

        if bands := asset_info.get("raster:bands"):
            stats = [
                (b["statistics"]["minimum"], b["statistics"]["maximum"])
                for b in bands
                if {"minimum", "maximum"}.issubset(b.get("statistics", {}))
            ]
            if len(stats) == len(bands):
                info["dataset_statistics"] = stats

        if vrt_options:
            # Construct VRT url
            info["url"] = f"vrt://{info['url']}?{vrt_options}"

        return info


@attr.s
class STACAPIBackend(BaseBackend):
    """STACAPI Mosaic Backend."""

    # STAC API URL
    stac_backend: STACBackend = attr.ib()

    # Because we are not using mosaicjson we are not limited to the WebMercator TMS
    tms: TileMatrixSet = attr.ib(default=WEB_MERCATOR_TMS)
    minzoom: int = attr.ib(default=None)
    maxzoom: int = attr.ib(default=None)

    # Use Custom STAC reader (outside init)
    reader: Type[SimpleSTACReader] = attr.ib(default=SimpleSTACReader)
    reader_options: Dict = attr.ib(factory=dict)

    # default values for bounds
    bounds: BBox = attr.ib(default=(-180, -90, 180, 90))

    crs: CRS = attr.ib(default=WGS84_CRS)
    geographic_crs: CRS = attr.ib(default=WGS84_CRS)

    # The reader is read-only (outside init)
    mosaic_def: MosaicJSON = attr.ib(init=False)

    _backend_name = "openEO"

    def __attrs_post_init__(self) -> None:
        """Post Init."""
        self.minzoom = self.minzoom if self.minzoom is not None else self.tms.minzoom
        self.maxzoom = self.maxzoom if self.maxzoom is not None else self.tms.maxzoom

        # Construct a FAKE mosaicJSON
        # mosaic_def has to be defined.
        # we set `tiles` to an empty list.
        self.mosaic_def = MosaicJSON(
            mosaicjson="0.0.3",
            name=self.input,
            bounds=self.bounds,
            minzoom=self.minzoom,
            maxzoom=self.maxzoom,
            tiles={},
        )

    def write(self, overwrite: bool = True) -> None:
        """This method is not used but is required by the abstract class."""
        pass

    def update(self) -> None:
        """We overwrite the default method."""
        pass

    def _read(self) -> MosaicJSON:
        """This method is not used but is required by the abstract class."""
        pass

    def assets_for_tile(
        self, x: int, y: int, z: int, query, **kwargs: Any
    ) -> List[Dict]:
        """Retrieve assets for tile."""
        bbox = self.tms.bounds(Tile(x, y, z))
        return self.get_assets(Polygon.from_bounds(*bbox), query, **kwargs)

    def assets_for_point(
        self,
        lng: float,
        lat: float,
        query: Dict,
        coord_crs: CRS = WGS84_CRS,
        **kwargs: Any,
    ) -> List[Dict]:
        """Retrieve assets for point."""
        if coord_crs != WGS84_CRS:
            xs, ys = transform(coord_crs, WGS84_CRS, [lng], [lat])
            lng, lat = xs[0], ys[0]

        return self.get_assets(
            Point(type="Point", coordinates=(lng, lat)), query, **kwargs
        )

    def assets_for_bbox(
        self,
        xmin: float,
        ymin: float,
        xmax: float,
        ymax: float,
        query: Dict,
        coord_crs: CRS = WGS84_CRS,
        **kwargs: Any,
    ) -> List[Dict]:
        """Retrieve assets for bbox."""
        if coord_crs != WGS84_CRS:
            xmin, ymin, xmax, ymax = transform_bounds(
                coord_crs,
                WGS84_CRS,
                xmin,
                ymin,
                xmax,
                ymax,
            )

        return self.get_assets(
            Polygon.from_bounds(xmin, ymin, xmax, ymax), query, **kwargs
        )

    @cached(  # type: ignore
        TTLCache(maxsize=cache_config.maxsize, ttl=cache_config.ttl),
        key=lambda self, geom, query, **kwargs: hashkey(
            self.stac_backend.url,
            str(geom),
            json.dumps(query),
            **kwargs,
        ),
    )
    def get_assets(
        self,
        geom: Geometry,
        query: Dict,
        fields: Optional[List[str]] = None,
    ) -> List[Dict]:
        """Find assets."""
        fields = fields or ["assets", "id", "bbox", "collection"]

        params = {
            **query,
            "intersects": geom.model_dump_json(exclude_none=True),
            "fields": fields,
        }
        params.pop("bbox", None)

        return self.stac_backend.get_items(**params)

    @property
    def _quadkeys(self) -> List[str]:
        return []

    def tile(
        self,
        tile_x: int,
        tile_y: int,
        tile_z: int,
        query: Dict,
        **kwargs: Any,
    ) -> Tuple[ImageData, List[str]]:
        """Get Tile from multiple observation."""
        mosaic_assets = self.assets_for_tile(
            tile_x,
            tile_y,
            tile_z,
            query,
        )

        if not mosaic_assets:
            raise NoAssetFoundError(
                f"No assets found for tile {tile_z}-{tile_x}-{tile_y}"
            )

        def _reader(
            item: Dict[str, Any], x: int, y: int, z: int, **kwargs: Any
        ) -> ImageData:
            with self.reader(item, tms=self.tms, **self.reader_options) as src_dst:
                return src_dst.tile(x, y, z, **kwargs)

        return mosaic_reader(mosaic_assets, _reader, tile_x, tile_y, tile_z, **kwargs)

    def point(
        self,
        lon: float,
        lat: float,
        query: Dict,
        coord_crs: CRS = WGS84_CRS,
        **kwargs: Any,
    ) -> List:
        """Get Point value from multiple observation."""
        raise NotImplementedError

    def part(
        self,
        bbox: BBox,
        query: Dict,
        dst_crs: Optional[CRS] = None,
        bounds_crs: CRS = WGS84_CRS,
        **kwargs: Any,
    ) -> Tuple[ImageData, List[str]]:
        """Create an Image from multiple items for a bbox."""
        xmin, ymin, xmax, ymax = bbox

        mosaic_assets = self.assets_for_bbox(
            xmin,
            ymin,
            xmax,
            ymax,
            query,
            coord_crs=bounds_crs,
        )

        if not mosaic_assets:
            raise NoAssetFoundError("No assets found for bbox input")

        def _reader(item: Dict[str, Any], bbox: BBox, **kwargs: Any) -> ImageData:
            with self.reader(item, **self.reader_options) as src_dst:
                return src_dst.part(bbox, **kwargs)

        return mosaic_reader(
            mosaic_assets,
            _reader,
            bbox,
            bounds_crs=bounds_crs,
            dst_crs=dst_crs or bounds_crs,
            **kwargs,
        )

    def feature(
        self,
        shape: Dict,
        query: Dict,
        dst_crs: Optional[CRS] = None,
        shape_crs: CRS = WGS84_CRS,
        max_size: int = 1024,
        **kwargs: Any,
    ) -> Tuple[ImageData, List[str]]:
        """Create an Image from multiple items for a GeoJSON feature."""
        if "geometry" in shape:
            shape = shape["geometry"]

        # PgSTAC needs geometry in WGS84
        shape_wgs84 = shape
        if shape_crs != WGS84_CRS:
            shape_wgs84 = transform_geom(shape_crs, WGS84_CRS, shape)

        mosaic_assets = self.get_assets(parse_geometry_obj(shape_wgs84), query)

        if not mosaic_assets:
            raise NoAssetFoundError("No assets found for Geometry")

        def _reader(item: Dict[str, Any], shape: Dict, **kwargs: Any) -> ImageData:
            with self.reader(item, **self.reader_options) as src_dst:
                return src_dst.feature(shape, **kwargs)

        return mosaic_reader(
            mosaic_assets,
            _reader,
            shape,
            shape_crs=shape_crs,
            dst_crs=dst_crs or shape_crs,
            max_size=max_size,
            **kwargs,
        )
