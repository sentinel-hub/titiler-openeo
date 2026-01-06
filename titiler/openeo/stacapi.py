"""Stac API backend."""

from typing import Any, Dict, List, Optional, Sequence, Union

import pyproj
import pystac
from attrs import define, field
from cachetools import TTLCache, cached
from cachetools.keys import hashkey
from openeo_pg_parser_networkx.pg_schema import BoundingBox, TemporalInterval
from pystac import Collection, Item
from pystac.extensions import datacube as dc
from pystac.extensions import eo
from pystac.extensions import item_assets as ia
from pystac_client import Client
from pystac_client.stac_api_io import StacApiIO
from rio_tiler.constants import MAX_THREADS
from rio_tiler.errors import TileOutsideBounds
from rio_tiler.mosaic.methods import PixelSelectionMethod
from rio_tiler.mosaic.reader import mosaic_reader
from rio_tiler.tasks import create_tasks
from urllib3 import Retry

from .errors import (
    ItemsLimitExceeded,
    NoDataAvailable,
    OutputLimitExceeded,
    STACLoadError,
    TemporalExtentEmpty,
    UnsupportedSTACObject,
)
from .processes.implementations.data_model import LazyRasterStack, RasterStack
from .processes.implementations.utils import _props_to_datetime, to_rasterio_crs
from .reader import _estimate_output_dimensions, _reader
from .settings import CacheSettings, ProcessingSettings, PySTACSettings

pystac_settings = PySTACSettings()
cache_config = CacheSettings()
processing_settings = ProcessingSettings()


@define
class stacApiBackend:
    """PySTAC-Client Backend."""

    url: str = field()
    _client_cache: Client = field(default=None, init=False)

    @property
    def client(self) -> Client:
        """Return a PySTAC-Client."""
        if not self._client_cache:
            stac_api_io = StacApiIO(
                max_retries=Retry(
                    total=pystac_settings.retry,
                    backoff_factor=pystac_settings.retry_factor,
                ),
            )
            self._client_cache = Client.open(self.url, stac_io=stac_api_io)
        return self._client_cache

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

        # bands
        if "item_assets" in collection.extra_fields:
            ia.ItemAssetsExtension.add_to(collection)
            bands_name = set()
            for key, asset in collection.ext.item_assets.items():
                if "data" in asset.properties.get("roles", []):
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
    ) -> List[Item]:
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
        return list(items.items())


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
        limit: Optional[int] = None,
        max_items: Optional[int] = None,
    ) -> List[Item]:
        fields = fields or [
            "assets",
            "id",
            "bbox",
            "collection",
            "properties",
            "type",
            "stac_version",
            "stac_extensions",
        ]

        query_params: Dict[str, Any] = {
            "collections": [id],
            "fields": fields,
            "limit": limit,
            "max_items": max_items,
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
            if temporal_extent.start is not None:
                start_date = str(temporal_extent.start.to_numpy())
            if temporal_extent.end is not None:
                end_date = str(temporal_extent.end.to_numpy())

            if not end_date and not start_date:
                raise TemporalExtentEmpty()

            # Create datetime string in the format "start_date/end_date"
            if start_date and end_date:
                query_params["datetime"] = f"{start_date}/{end_date}"
            elif start_date:
                query_params["datetime"] = f"{start_date}/.."
            elif end_date:
                query_params["datetime"] = f"../{end_date}"

        if properties is not None:
            # Convert OpenEO process graphs to STAC CQL2-JSON format
            filter_expr = self._convert_process_graph_to_cql2(properties)
            query_params["filter"] = filter_expr
            query_params["filter_lang"] = "cql2-json"

        return self.stac_api.get_items(**query_params)

    def _handle_comparison_operator(
        self, process_id: str, prop_name: str, args: dict
    ) -> Optional[Dict]:
        """Handle comparison operators like eq, lt, gt, etc."""
        operators = {
            "eq": "=",
            "neq": "<>",
            "lt": "<",
            "lte": "<=",
            "gt": ">",
            "gte": ">=",
        }

        if process_id in operators:
            return {
                "op": operators[process_id],
                "args": [{"property": f"{prop_name}"}, args.get("y")],
            }

        if process_id == "between":
            return {
                "op": "between",
                "args": [
                    {"property": f"{prop_name}"},
                    args.get("min"),
                    args.get("max"),
                ],
            }

        return None

    def _handle_array_operator(
        self, process_id: str, prop_name: str, args: dict
    ) -> Optional[Dict]:
        """Handle array operators like in, array_contains."""
        if process_id in ["in", "array_contains"]:
            return {
                "op": "in",
                "args": [
                    {"property": f"{prop_name}"},
                    {"array": args.get("values", [])},
                ],
            }
        return None

    def _handle_pattern_operator(
        self, process_id: str, prop_name: str, args: dict
    ) -> Optional[Dict]:
        """Handle pattern matching operators like starts_with, ends_with, contains."""
        if process_id == "starts_with":
            pattern = args.get("y", "") + "%"
            return {
                "op": "like",
                "args": [{"property": f"{prop_name}"}, pattern],
            }
        elif process_id == "ends_with":
            pattern = "%" + args.get("y", "")
            return {
                "op": "like",
                "args": [{"property": f"{prop_name}"}, pattern],
            }
        elif process_id == "contains":
            pattern = "%" + args.get("y", "") + "%"
            return {
                "op": "like",
                "args": [{"property": f"{prop_name}"}, pattern],
            }
        return None

    def _handle_null_check(self, process_id: str, prop_name: str) -> Optional[Dict]:
        """Handle null check operators."""
        if process_id == "is_null":
            return {
                "op": "isNull",
                "args": [{"property": f"{prop_name}"}],
            }
        return None

    def _handle_logical_operator(
        self, process_id: str, prop_name: str, args: dict, node_id: str
    ) -> Optional[Dict]:
        """Handle logical operators like and, or, not."""
        if process_id not in ["and", "or", "not"]:
            return None

        if process_id in ["and", "or"]:
            sub_conditions = []
            for sub_arg in args.get("expressions", []):
                # Recursively process sub-conditions
                sub_pg = {"process_graph": {f"sub_{node_id}": sub_arg}}
                sub_condition = self._convert_process_graph_to_cql2({prop_name: sub_pg})
                if sub_condition:
                    sub_conditions.append(sub_condition)

            if sub_conditions:
                return {"op": process_id, "args": sub_conditions}

        elif process_id == "not":
            # Recursively process the negated condition
            sub_pg = {"process_graph": {f"sub_{node_id}": args.get("expression")}}
            sub_condition = self._convert_process_graph_to_cql2({prop_name: sub_pg})
            if sub_condition:
                return {"op": "not", "args": [sub_condition]}

        return None

    def _handle_default_operator(self, prop_name: str, args: dict) -> Optional[Dict]:
        """Handle default case for unmatched processes."""
        # Try to extract target value from arguments
        target_value = None
        for _, arg_value in args.items():
            if (
                isinstance(arg_value, dict)
                and arg_value.get("from_parameter") == "value"
            ):
                continue
            else:
                target_value = arg_value
                break

        if target_value is not None:
            return {
                "op": "=",
                "args": [{"property": f"properties.{prop_name}"}, target_value],
            }
        return None

    def _handle_direct_value(self, prop_name: str, value) -> Dict:
        """Handle non-process graph case (direct value)."""
        return {"op": "=", "args": [{"property": f"{prop_name}"}, value]}

    def _process_single_property(self, prop_name: str, process_graph) -> Optional[Dict]:
        """Process a single property in the process graph."""
        # Handle non-process graph case (direct value)
        if not isinstance(process_graph, dict) or "process_graph" not in process_graph:
            return self._handle_direct_value(prop_name, process_graph)

        # Extract process graph
        pg = process_graph["process_graph"]
        if not pg:
            return None

        # Get the node ID and node
        node_id = next(iter(pg.keys()))
        node = pg[node_id]
        process_id = node.get("process_id")
        args = node.get("arguments", {})

        # Try each operator type handler
        handlers = [
            self._handle_comparison_operator,
            self._handle_array_operator,
            self._handle_pattern_operator,
            self._handle_null_check,
            lambda p_id, p_name, a: self._handle_logical_operator(
                p_id, p_name, a, node_id
            ),
        ]

        for handler in handlers:
            # Call handler without checking process_id type first
            condition = handler(process_id, prop_name, args)  # type: ignore
            if condition:
                return condition

        # Try default handler as fallback
        return self._handle_default_operator(prop_name, args)

    def _convert_process_graph_to_cql2(self, properties: dict) -> dict:
        """
        Convert OpenEO process graph properties to STAC CQL2-JSON format.

        Args:
            properties: Dictionary of property name to OpenEO process graph

        Returns:
            Dictionary in CQL2-JSON format following the STAC API Filter Extension spec
        """
        if not properties:
            return {}

        # For single property we return the condition directly
        if len(properties) == 1:
            prop_name = next(iter(properties.keys()))
            condition = self._process_single_property(prop_name, properties[prop_name])
            return condition if condition else {}

        # For multiple properties, combine with AND
        cql2_filter: Dict[str, Any] = {"op": "and", "args": []}
        for prop_name, process_graph in properties.items():
            condition = self._process_single_property(prop_name, process_graph)
            if condition:
                cql2_filter["args"].append(condition)

        return cql2_filter

    def load_collection(
        self,
        id: str,
        spatial_extent: Optional[BoundingBox] = None,
        temporal_extent: Optional[TemporalInterval] = None,
        bands: Optional[list[str]] = None,
        properties: Optional[dict] = None,
        # private arguments
        width: Optional[int] = 1024,
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

        # Check the items limit
        if len(items) > processing_settings.max_items:
            raise ItemsLimitExceeded(len(items), processing_settings.max_items)

        # Check pixel limit before calling _estimate_output_dimensions
        # For test_load_collection_pixel_threshold
        if width and height:
            width_int = int(width)
            height_int = int(height)
            pixel_count = width_int * height_int * len(items)
            if pixel_count > processing_settings.max_pixels:
                raise OutputLimitExceeded(
                    width_int,
                    height_int,
                    processing_settings.max_pixels,
                    items_count=len(items),
                )

        # If bands parameter is missing, use the first asset from the first item
        if bands is None and items and items[0].assets:
            bands = list(items[0].assets.keys())[:1]  # Take the first asset as default

        # Estimate dimensions based on items and spatial extent
        dimensions = _estimate_output_dimensions(
            items, spatial_extent, bands, width, height
        )

        # Extract values from the result
        width = dimensions["width"]
        height = dimensions["height"]
        bbox = dimensions["bbox"]
        crs = dimensions["crs"]

        # Group items by date
        items_by_date: dict[str, list[dict]] = {}
        for item in items:
            date = item.datetime.isoformat()
            if date not in items_by_date:
                items_by_date[date] = []
            items_by_date[date].append(item)

        # Create a RasterStack with merged items for each date
        result = {}
        for date, date_items in items_by_date.items():
            img, _ = mosaic_reader(
                date_items,
                _reader,
                bbox,
                bounds_crs=crs,
                assets=bands,
                dst_crs=crs,
                width=int(width) if width else width,
                height=int(height) if height else height,
                buffer=float(tile_buffer) if tile_buffer is not None else tile_buffer,
                pixel_selection=PixelSelectionMethod["first"].value(),
            )
            result[date] = img

        return result

    def load_collection_and_reduce(
        self,
        id: str,
        spatial_extent: Optional[BoundingBox] = None,
        temporal_extent: Optional[TemporalInterval] = None,
        bands: Optional[list[str]] = None,
        properties: Optional[dict] = None,
        pixel_selection: Optional[str] = "first",
        # private arguments
        width: Optional[int] = 1024,
        height: Optional[int] = None,
        tile_buffer: Optional[float] = None,
    ) -> RasterStack:
        """Load Collection and return image."""
        items = self._get_items(
            id,
            spatial_extent=spatial_extent,
            temporal_extent=temporal_extent,
            properties=properties,
        )
        if not items:
            raise NoDataAvailable("There is no data available for the given extents.")

        # Check the items limit
        if len(items) > processing_settings.max_items:
            raise ItemsLimitExceeded(len(items), processing_settings.max_items)

        # Check pixel limit before calling _estimate_output_dimensions
        # For test_load_collection_and_reduce_pixel_threshold
        if width and height:
            width_int = int(width)
            height_int = int(height)
            pixel_count = width_int * height_int * len(items)
            if pixel_count > processing_settings.max_pixels:
                raise OutputLimitExceeded(
                    width_int,
                    height_int,
                    processing_settings.max_pixels,
                    items_count=len(items),
                )

        # If bands parameter is missing, use the first asset from the first item
        if bands is None and items and items[0].assets:
            bands = list(items[0].assets.keys())[:1]  # Take the first asset as default

        # Estimate dimensions based on items and spatial extent
        dimensions = _estimate_output_dimensions(
            items, spatial_extent, bands, width, height
        )

        # Extract values from the result
        width = dimensions["width"]
        height = dimensions["height"]
        bbox = dimensions["bbox"]
        crs = dimensions["crs"]

        img, _ = mosaic_reader(
            items,
            _reader,
            bbox,
            bounds_crs=crs,
            assets=bands,
            dst_crs=crs,
            width=int(width) if width else width,
            height=int(height) if height else height,
            buffer=float(tile_buffer) if tile_buffer is not None else tile_buffer,
            pixel_selection=PixelSelectionMethod[pixel_selection or "first"].value(),
        )
        # Return a RasterStack with a single entry
        # Use a consistent key naming approach
        key = "reduced"
        if temporal_extent and temporal_extent[0]:
            key = str(temporal_extent[0].to_numpy())
        elif items:
            key = items[0].datetime.isoformat()

        return {key: img}


@define
class LoadStac:
    """Backend Specific STAC loaders."""

    def _load_stac_object(self, url: str) -> pystac.STACObject:
        """Load a STAC object from a URL.

        Args:
            url: URL to a static STAC catalog or a STAC API Collection

        Returns:
            The loaded STAC object

        Raises:
            ValueError: If the STAC object cannot be loaded
        """
        try:
            return pystac.read_file(url)
        except Exception as e:
            raise STACLoadError(url=url, error=str(e)) from e

    def _handle_collection_or_catalog(
        self,
        stac_obj: Union[pystac.Collection, pystac.Catalog],
        spatial_extent: Optional[BoundingBox] = None,
        temporal_extent: Optional[TemporalInterval] = None,
        bands: Optional[list[str]] = None,
        properties: Optional[dict] = None,
        width: Optional[int] = None,
        height: Optional[int] = None,
        tile_buffer: Optional[float] = None,
    ) -> RasterStack:
        """Handle a STAC Collection or Catalog.

        Args:
            stac_obj: The STAC Collection or Catalog
            spatial_extent: Optional bounding box to limit the data
            temporal_extent: Optional temporal interval to limit the data
            bands: Optional list of band names to include
            properties: Optional metadata properties to filter by
            width: Optional width of the output image in pixels
            height: Optional height of the output image in pixels
            tile_buffer: Optional buffer around the tile in pixels

        Returns:
            A RasterStack containing the loaded data
        """
        collection_id = stac_obj.id
        stac_api = stacApiBackend(url=stac_obj.get_root_link().href)
        load_collection = LoadCollection(stac_api=stac_api)

        return load_collection.load_collection(
            id=collection_id,
            spatial_extent=spatial_extent,
            temporal_extent=temporal_extent,
            bands=bands,
            properties=properties,
            width=width,
            height=height,
            tile_buffer=tile_buffer,
        )

    def _filter_by_temporal_extent(
        self, items: List[Dict], temporal_extent: TemporalInterval
    ) -> List[Dict]:
        """Filter items by temporal extent.

        Args:
            items: List of STAC items
            temporal_extent: Temporal interval to filter by

        Returns:
            Filtered list of items

        Raises:
            TemporalExtentEmpty: If the temporal extent is empty
        """
        start_date = None
        end_date = None
        if temporal_extent[0] is not None:
            start_date = str(temporal_extent[0].to_numpy())
        if temporal_extent[1] is not None:
            end_date = str(temporal_extent[1].to_numpy())

        if not end_date and not start_date:
            raise TemporalExtentEmpty()

        filtered_items = []
        for item in items:
            item_datetime = item.get("properties", {}).get("datetime")
            if not item_datetime:
                continue

            if start_date and end_date:
                if start_date <= item_datetime < end_date:
                    filtered_items.append(item)
            elif start_date:
                if start_date <= item_datetime:
                    filtered_items.append(item)
            elif end_date:
                if item_datetime < end_date:
                    filtered_items.append(item)

        return filtered_items

    def _filter_by_properties(self, items: List[Dict], properties: dict) -> List[Dict]:
        """Filter items by properties.

        Args:
            items: List of STAC items
            properties: Properties to filter by

        Returns:
            Filtered list of items
        """
        filtered_items = []
        for item in items:
            include = True
            for prop_name, prop_value in properties.items():
                if (
                    prop_name not in item.get("properties", {})
                    or item["properties"][prop_name] != prop_value
                ):
                    include = False
                    break
            if include:
                filtered_items.append(item)
        return filtered_items

    def _process_spatial_extent(
        self,
        items: List[Dict],
        spatial_extent: BoundingBox,
        bands: Optional[list[str]] = None,
        width: Optional[int] = None,
        height: Optional[int] = None,
        tile_buffer: Optional[float] = None,
    ) -> RasterStack:
        """Process spatial extent and create tasks.

        Args:
            items: List of STAC items
            spatial_extent: Bounding box to limit the data
            bands: Optional list of band names to include
            width: Optional width of the output image in pixels
            height: Optional height of the output image in pixels
            tile_buffer: Optional buffer around the tile in pixels

        Returns:
            A LazyRasterStack containing the tasks

        Raises:
            NotImplementedError: If spatial_extent is not provided
        """
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

        return LazyRasterStack(
            tasks=tasks,
            key_fn=lambda asset: asset["id"],  # Use item ID as unique key
            timestamp_fn=lambda asset: _props_to_datetime(asset["properties"]),
            allowed_exceptions=(TileOutsideBounds,),
        )

    def load_stac(
        self,
        url: str,
        spatial_extent: Optional[BoundingBox] = None,
        temporal_extent: Optional[TemporalInterval] = None,
        bands: Optional[list[str]] = None,
        properties: Optional[dict] = None,
        # private arguments
        width: Optional[int] = None,
        height: Optional[int] = None,
        tile_buffer: Optional[float] = None,
    ) -> RasterStack:
        """Load data from a STAC catalog or API.

        Args:
            url: URL to a static STAC catalog or a STAC API Collection
            spatial_extent: Optional bounding box to limit the data
            temporal_extent: Optional temporal interval to limit the data
            bands: Optional list of band names to include
            properties: Optional metadata properties to filter by
            width: Optional width of the output image in pixels
            height: Optional height of the output image in pixels
            tile_buffer: Optional buffer around the tile in pixels

        Returns:
            A RasterStack containing the loaded data

        Raises:
            NoDataAvailable: If no data is available for the given extents
            TemporalExtentEmpty: If the temporal extent is empty
            NotImplementedError: If spatial_extent is not provided
        """
        # Load the STAC catalog or item from the URL
        stac_obj = self._load_stac_object(url)

        # If the STAC object is a Collection or Catalog, use load_collection
        if isinstance(stac_obj, (pystac.Collection, pystac.Catalog)):
            return self._handle_collection_or_catalog(
                stac_obj,
                spatial_extent=spatial_extent,
                temporal_extent=temporal_extent,
                bands=bands,
                properties=properties,
                width=width,
                height=height,
                tile_buffer=tile_buffer,
            )

        # For a single item, use it directly
        elif isinstance(stac_obj, pystac.Item):
            items = [stac_obj.to_dict()]
        else:
            raise UnsupportedSTACObject(str(type(stac_obj)))

        if not items:
            raise NoDataAvailable("There is no data available in the STAC catalog.")

        # Filter items by temporal extent if provided
        if temporal_extent is not None:
            items = self._filter_by_temporal_extent(items, temporal_extent)

        # Filter items by properties if provided
        if properties is not None:
            items = self._filter_by_properties(items, properties)

        if not items:
            raise NoDataAvailable("There is no data available for the given extents.")

        # Process spatial extent
        if spatial_extent:
            return self._process_spatial_extent(
                items,
                spatial_extent,
                bands=bands,
                width=width,
                height=height,
                tile_buffer=tile_buffer,
            )

        # Estimate dimensions based on items and spatial extent
        dimensions = _estimate_output_dimensions(
            items, spatial_extent, bands, width, height
        )

        # Extract values from the result
        width = dimensions["width"]
        height = dimensions["height"]
        bbox = dimensions["bbox"]
        crs = dimensions["crs"]

        img, _ = mosaic_reader(
            items,
            _reader,
            bbox,
            bounds_crs=crs,
            dst_crs=crs,
            width=int(width) if width else width,
            height=int(height) if height else height,
            buffer=float(tile_buffer) if tile_buffer is not None else tile_buffer,
        )
        return img
