"""titiler.openeo.processes."""

from typing import Any, Dict, List, Optional

import pyproj
from openeo_pg_parser_networkx.pg_schema import BoundingBox, TemporalInterval
from rio_tiler.constants import MAX_THREADS
from rio_tiler.errors import TileOutsideBounds
from rio_tiler.models import ImageData
from rio_tiler.mosaic.methods import PixelSelectionMethod
from rio_tiler.mosaic.reader import mosaic_reader
from rio_tiler.tasks import create_tasks, filter_tasks
from rio_tiler.types import BBox

from ...stacapi import stac_backend
from .data_model import RasterStack
from .errors import NoDataAvailable, TemporalExtentEmpty
from .reader import SimpleSTACReader
from .utils import to_rasterio_crs

__all__ = ["load_collection", "load_collection_and_reduce", "save_result"]


def get_items(
    collection_id: str,
    spatial_extent: Optional[BoundingBox] = None,
    temporal_extent: Optional[TemporalInterval] = None,
    properties: Optional[dict] = None,
    fields: Optional[List[str]] = None,
) -> List[Dict]:
    fields = fields or ["assets", "id", "bbox", "collection", "properties"]

    query_params: Dict[str, Any] = {"collections": [collection_id], "fields": fields}

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
            raise TemporalExtentEmpty(
                "The temporal extent is empty. The second instant in time must always be greater/later than the first instant in time."
            )

        query_params["datetime"] = [start_date, end_date]

    if properties is not None:
        query_params["query"] = properties

    return stac_backend.get_items(**query_params)


def _props_to_datename(props: Dict) -> str:
    if d := props["datetime"]:
        return d

    start_date = props["start_datetime"]
    end_date = props["end_datetime"]
    return start_date if start_date else end_date


def load_collection(
    collection_id: str,
    spatial_extent: Optional[BoundingBox] = None,
    temporal_extent: Optional[TemporalInterval] = None,
    bands: Optional[list[str]] = None,
    properties: Optional[dict] = None,
    width: Optional[int] = None,
    height: Optional[int] = None,
) -> RasterStack:
    """Load Collection."""
    items = get_items(
        collection_id,
        spatial_extent=spatial_extent,
        temporal_extent=temporal_extent,
        properties=properties,
    )
    if not items:
        raise NoDataAvailable("There is no data available for the given extents.")

    if spatial_extent:

        def _reader(item: Dict[str, Any], bbox: BBox, **kwargs: Any) -> ImageData:
            with SimpleSTACReader(item) as src_dst:
                return src_dst.part(*bbox, **kwargs)

        bbox = [
            spatial_extent.west,
            spatial_extent.south,
            spatial_extent.east,
            spatial_extent.north,
        ]
        projcrs = pyproj.crs.CRS(spatial_extent.crs or "epsg:4326")
        crs = to_rasterio_crs(projcrs)

        # TODO: convert `bands` into assets
        tasks = create_tasks(
            _reader,
            items,
            MAX_THREADS,
            bbox,
            bounds_crs=crs,
            dst_crs=crs,
            width=width,
            height=height,
        )
        return {
            _props_to_datename(asset["properties"]): val
            for val, asset in filter_tasks(
                tasks, allowed_exceptions=(TileOutsideBounds,)
            )
        }

    raise NotImplementedError("Can't use this backend without spatial extent")


def load_collection_and_reduce(
    collection_id: str,
    spatial_extent: Optional[BoundingBox] = None,
    temporal_extent: Optional[TemporalInterval] = None,
    bands: Optional[list[str]] = None,
    properties: Optional[dict] = None,
    pixel_selection: Optional[str] = "first",
    width: Optional[int] = None,
    height: Optional[int] = None,
) -> ImageData:
    """Load Collection and return image."""
    items = get_items(
        collection_id,
        spatial_extent=spatial_extent,
        temporal_extent=temporal_extent,
        properties=properties,
    )
    if not items:
        raise NoDataAvailable("There is no data available for the given extents.")

    if spatial_extent:

        def _reader(item: Dict[str, Any], bbox: BBox, **kwargs: Any) -> ImageData:
            with SimpleSTACReader(item) as src_dst:
                return src_dst.part(*bbox, **kwargs)

        bbox = [
            spatial_extent.west,
            spatial_extent.south,
            spatial_extent.east,
            spatial_extent.north,
        ]
        projcrs = pyproj.crs.CRS(spatial_extent.crs or "epsg:4326")
        crs = to_rasterio_crs(projcrs)

        # TODO: convert `bands` into assets
        img, _ = mosaic_reader(
            items,
            _reader,
            bbox,
            bounds_crs=crs,
            dst_crs=crs,
            pixel_selection=PixelSelectionMethod[pixel_selection].value(),
            width=width,
            height=height,
        )
        return img

    raise NotImplementedError("Can't use this backend without spatial extent")


def save_result(
    data: ImageData,
    format: str,
    options: Optional[Dict] = None,
) -> bytes:
    """Save Result."""
    options = options or {}
    return data.render(img_format=format, **options)
