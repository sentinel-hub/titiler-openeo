"""titiler.openeo.processes Apply."""

from typing import Any, Callable, Dict, Optional
import morecantile
from numpy.typing import ArrayLike
from openeo_pg_parser_networkx.pg_schema import BoundingBox

from .data_model import ImageData, RasterStack

__all__ = ["apply", "xyz_to_extent"]


def apply(
    data: RasterStack,
    process: Callable,
    context: Optional[Dict] = None,
) -> RasterStack:
    """Apply process on RasterStack."""
    positional_parameters = {"x": 0}
    named_parameters = {"context": context}

    def _process_img(img: ImageData):
        return ImageData(
            process(
                img.array,
                positional_parameters=positional_parameters,
                named_parameters=named_parameters,
            ),
            assets=img.assets,
            crs=img.crs,
            bounds=img.bounds,
        )

    # Apply process to each item in the stack
    return {k: _process_img(img) for k, img in data.items()}


def xyz_to_extent(
    x: Any,
    context: Optional[Dict] = None,
) -> RasterStack:
    """Apply process on ArrayLike."""

    tile: morecantile.Tile = x
    tilematrixset = "WebMercatorQuad"
    tms = morecantile.tms.get(tilematrixset)
    tile_bounds = list(tms.xy_bounds(morecantile.Tile(x=tile.x, y=tile.y, z=tile.z)))
    bbox = BoundingBox(
        west=tile_bounds[0],
        south=tile_bounds[1],
        east=tile_bounds[2],
        north=tile_bounds[3],
        crs=tms.crs.to_epsg() or tms.crs.to_wkt(),
    )

    return bbox
