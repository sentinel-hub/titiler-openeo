"""Stac API backend."""

from typing import Any, Dict, List, Optional, Sequence, Union

import pyproj
from attrs import define, field
from cachetools import TTLCache, cached
from cachetools.keys import hashkey
from openeo_pg_parser_networkx.pg_schema import BoundingBox, TemporalInterval
from pystac import Collection
from pystac.extensions import datacube as dc
from pystac.extensions import eo
from pystac.extensions import item_assets as ia
from pystac_client import Client
from pystac_client.stac_api_io import StacApiIO
from rio_tiler.constants import MAX_THREADS
from rio_tiler.errors import TileOutsideBounds
from rio_tiler.models import ImageData
from rio_tiler.mosaic.methods import PixelSelectionMethod
from rio_tiler.mosaic.reader import mosaic_reader
from rio_tiler.tasks import create_tasks, filter_tasks
from rio_tiler.types import BBox
from urllib3 import Retry

from .errors import NoDataAvailable, TemporalExtentEmpty
from .processes.implementations.data_model import RasterStack
from .processes.implementations.utils import _props_to_datename, to_rasterio_crs
from .reader import SimpleSTACReader
from .settings import CacheSettings, PySTACSettings

pystac_settings = PySTACSettings()
cache_config = CacheSettings()


@define
class stacApiBackend:
    """PySTAC-Client Backend."""

    url: str = field()
    client: Client = field(default=None)

    def __attrs_post_init__(self) -> None:
        """Create Client."""
        if not self.client:
            stac_api_io = StacApiIO(
                max_retries=Retry(
                    total=pystac_settings.retry,
                    backoff_factor=pystac_settings.retry_factor,
                ),
            )
            self.client = Client.open(self.url, stac_io=stac_api_io)

    @cached(  # type: ignore
        TTLCache(maxsize=cache_config.maxsize, ttl=cache_config.ttl),
        key=lambda self, **kwargs: hashkey(self.url, **kwargs),
    )
    def get_collections(self, **kwargs) -> List[Dict]:
        """Return List of STAC Collections."""
        collections = []
        for collection in self.client.get_collections():
            collection = self.add_version_if_missing(collection)
            collection = self.add_data_cubes_if_missing(collection)
            collections.append(collection)
        return [col.to_dict() for col in collections]

    def add_version_if_missing(self, collection: Collection):
        """Add version to collection if missing."""
        if not collection.ext.has("version"):
            collection.ext.add("version")
        if not collection.ext.version.version:
            collection.ext.version.version = "1.0.0"
        return collection

    def add_data_cubes_if_missing(self, collection: Collection):
        """Add datacubes extension to collection if missing."""
        if not collection.ext.has("cube"):
            dc.DatacubeExtension.add_to(collection)
            """ Add minimal dimensions """
            collection.ext.cube.apply(
                dimensions=self.getdimensions(collection),
                # variables=self.getvariables(collection),
            )

        return collection

    def getdimensions(self, collection: Collection) -> Dict[str, dc.Dimension]:
        """Get dimensions from collection"""
        dims = {}

        # Spatial extent
        if collection.extent.spatial.bboxes:
            dims["x"] = dc.Dimension.from_dict(
                {
                    "type": "spatial",
                    "axis": "x",
                    "extent": [
                        collection.extent.spatial.bboxes[0][0],
                        collection.extent.spatial.bboxes[0][2],
                    ],
                }
            )
            dims["y"] = dc.Dimension.from_dict(
                {
                    "type": "spatial",
                    "axis": "y",
                    "extent": [
                        collection.extent.spatial.bboxes[0][1],
                        collection.extent.spatial.bboxes[0][3],
                    ],
                }
            )

        # Temporal extent
        if collection.extent.temporal.intervals:
            dims["t"] = dc.Dimension.from_dict(
                {
                    "type": "temporal",
                    "extent": [
                        collection.extent.temporal.intervals[0][0],
                        collection.extent.temporal.intervals[0][1],
                    ],
                }
            )

        # Spectral bands
        # TEMP FIX: The item_assets in core collection is not supported in PySTAC yet.
        if (
            eo.EOExtension.has_extension(collection)
            and "item_assets" in collection.extra_fields
        ):
            ia.ItemAssetsExtension.add_to(collection)
            bands_name = set()
            for key, asset in collection.ext.item_assets.items():
                if asset.properties.get("bands", None) or asset.properties.get(
                    "eo:common_name", None
                ):
                    bands_name.add(key)
            if len(bands_name) > 0:
                dims["spectral"] = dc.Dimension.from_dict(
                    {
                        "type": "bands",
                        "values": bands_name,
                    }
                )

        return dims

    def getvariables(self, collection: Collection) -> Dict[str, dc.Variable]:
        """Get variables from collection"""
        variables = {}
        if eo.EOExtension.has_extension(collection):
            eo_ext = eo.EOExtension.ext(collection)
            if eo_ext.bands:
                for band in eo_ext.bands:
                    variables[band.name] = dc.Variable.from_dict(
                        {
                            "name": band.name,
                            "description": band.description,
                            "unit": band.unit,
                            "data_type": band.data_type,
                        }
                    )
        return variables

    @cached(  # type: ignore
        TTLCache(maxsize=cache_config.maxsize, ttl=cache_config.ttl),
        key=lambda self, collection_id, **kwargs: hashkey(
            self.url, collection_id, **kwargs
        ),
    )
    def get_collection(self, collection_id: str, **kwargs) -> Dict:
        """Return STAC Collection"""
        col = self.client.get_collection(collection_id)
        col = self.add_version_if_missing(col)
        col = self.add_data_cubes_if_missing(col)
        return col.to_dict()

    def get_items(
        self,
        collections: List[str],
        ids: Optional[List[str]] = None,
        bbox: Optional[Sequence[float]] = None,
        intersects: Optional[Dict] = None,
        datetime: Optional[Union[str, Sequence[str]]] = None,
        query: Optional[Union[List, Dict]] = None,
        filter: Optional[Dict] = None,
        filter_lang: str = "cql2-json",
        sortby: Optional[Union[str, List[str]]] = None,
        fields: Optional[List[str]] = None,
        limit: Optional[int] = None,
        max_items: Optional[int] = None,
        **kwargs,
    ) -> List[Dict]:
        """Return List of STAC Items."""
        limit = limit or 100
        max_items = max_items or 100

        items = self.client.search(
            collections=collections,
            ids=ids,
            bbox=bbox,
            intersects=intersects,
            datetime=datetime,
            query=query,
            filter=filter,
            filter_lang=filter_lang,
            sortby=sortby,
            fields=fields,
            limit=limit,
            max_items=max_items,
        )
        return list(items.items_as_dicts())


@define
class LoadCollection:
    """Backend Specific Collection loaders."""

    stac_api: stacApiBackend = field()

    def _get_items(
        self,
        id: str,
        spatial_extent: Optional[BoundingBox] = None,
        temporal_extent: Optional[TemporalInterval] = None,
        properties: Optional[dict] = None,
        fields: Optional[List[str]] = None,
    ) -> List[Dict]:
        fields = fields or ["assets", "id", "bbox", "collection", "properties"]

        query_params: Dict[str, Any] = {
            "collections": [id],
            "fields": fields,
        }

        if spatial_extent is not None:
            bbox = [
                spatial_extent.west,
                spatial_extent.south,
                spatial_extent.east,
                spatial_extent.north,
            ]

            crs = pyproj.crs.CRS(spatial_extent.crs or "epsg:4326")
            if not crs.equals("EPSG:4326"):
                trans = pyproj.Transformer.from_crs(
                    crs,
                    pyproj.CRS.from_epsg(4326),
                    always_xy=True,
                )
                bbox = trans.transform_bounds(*bbox, densify_pts=21)

            query_params["bbox"] = bbox

        if temporal_extent is not None:
            start_date = None
            end_date = None
            if temporal_extent[0] is not None:
                start_date = str(temporal_extent[0].to_numpy())
            if temporal_extent[1] is not None:
                end_date = str(temporal_extent[1].to_numpy())

            if not end_date and not start_date:
                raise TemporalExtentEmpty()

            query_params["datetime"] = [start_date, end_date]

        if properties is not None:
            query_params["query"] = properties

        return self.stac_api.get_items(**query_params)

    def load_collection(
        self,
        id: str,
        spatial_extent: Optional[BoundingBox] = None,
        temporal_extent: Optional[TemporalInterval] = None,
        bands: Optional[list[str]] = None,
        properties: Optional[dict] = None,
        # private arguments
        width: Optional[int] = None,
        height: Optional[int] = None,
        tile_buffer: Optional[float] = None,
    ) -> RasterStack:
        """Load Collection."""
        items = self._get_items(
            id,
            spatial_extent=spatial_extent,
            temporal_extent=temporal_extent,
            properties=properties,
        )
        if not items:
            raise NoDataAvailable("There is no data available for the given extents.")

        # TODO:
        # - Get PROJ information about the Items
        # - Estimate output size in Pixel and raise issue if too big

        if spatial_extent:

            def _reader(item: Dict[str, Any], bbox: BBox, **kwargs: Any) -> ImageData:
                with SimpleSTACReader(item) as src_dst:
                    return src_dst.part(bbox, **kwargs)

            bbox = [
                spatial_extent.west,
                spatial_extent.south,
                spatial_extent.east,
                spatial_extent.north,
            ]
            projcrs = pyproj.crs.CRS(spatial_extent.crs or "epsg:4326")
            crs = to_rasterio_crs(projcrs)

            tasks = create_tasks(
                _reader,
                items,
                MAX_THREADS,
                bbox,
                assets=bands,
                bounds_crs=crs,
                dst_crs=crs,
                width=int(width) if width else width,
                height=int(height) if height else height,
                buffer=float(tile_buffer) if tile_buffer is not None else tile_buffer,
            )
            return {
                _props_to_datename(asset["properties"]): val
                for val, asset in filter_tasks(
                    tasks, allowed_exceptions=(TileOutsideBounds,)
                )
            }

        raise NotImplementedError("Can't use this backend without spatial extent")

    def load_collection_and_reduce(
        self,
        id: str,
        spatial_extent: Optional[BoundingBox] = None,
        temporal_extent: Optional[TemporalInterval] = None,
        bands: Optional[list[str]] = None,
        properties: Optional[dict] = None,
        pixel_selection: Optional[str] = "first",
        # private arguments
        width: Optional[int] = None,
        height: Optional[int] = None,
        tile_buffer: Optional[float] = None,
    ) -> ImageData:
        """Load Collection and return image."""
        items = self._get_items(
            id,
            spatial_extent=spatial_extent,
            temporal_extent=temporal_extent,
            properties=properties,
        )
        if not items:
            raise NoDataAvailable("There is no data available for the given extents.")

        # TODO #18:
        # - Get PROJ information about the Items
        # - Estimate output size in Pixel and raise issue if too big

        if spatial_extent:

            def _reader(item: Dict[str, Any], bbox: BBox, **kwargs: Any) -> ImageData:
                with SimpleSTACReader(item) as src_dst:
                    return src_dst.part(
                        bbox,
                        assets=bands or list(items[0]["assets"]),
                        **kwargs,
                    )

            bbox = [
                spatial_extent.west,
                spatial_extent.south,
                spatial_extent.east,
                spatial_extent.north,
            ]
            projcrs = pyproj.crs.CRS(spatial_extent.crs or "epsg:4326")
            crs = to_rasterio_crs(projcrs)

            img, _ = mosaic_reader(
                items,
                _reader,
                bbox,
                bounds_crs=crs,
                dst_crs=crs,
                width=int(width) if width else width,
                height=int(height) if height else height,
                buffer=float(tile_buffer) if tile_buffer is not None else tile_buffer,
                pixel_selection=PixelSelectionMethod[pixel_selection].value(),
            )
            return img

        raise NotImplementedError("Can't use this backend without spatial extent")
