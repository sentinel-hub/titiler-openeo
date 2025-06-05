"""titiler.openeo.processes Apply."""

from typing import Any, Callable, Dict, Optional

import morecantile

from titiler.openeo.models.openapi import SpatialExtent

from .data_model import ImageData, RasterStack

__all__ = ["apply", "xyz_to_bbox"]


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


def xyz_to_bbox(
    data: Dict[str, Any],
    context: Optional[Dict] = None,
) -> SpatialExtent:
    """Apply process on ArrayLike."""

    # find x, y and z attributes
    if not all(k in data for k in ["x", "y", "z"]):
        raise ValueError("Missing x, y or z attributes in data")
    tile: morecantile.Tile = morecantile.Tile(
        x=data["x"],
        y=data["y"],
        z=data["z"],
    )
    tilematrixset = "WebMercatorQuad"
    tms = morecantile.tms.get(tilematrixset)
    tile_bounds = list(tms.xy_bounds(morecantile.Tile(x=tile.x, y=tile.y, z=tile.z)))
    bbox = SpatialExtent(
        west=tile_bounds[0],
        south=tile_bounds[1],
        east=tile_bounds[2],
        north=tile_bounds[3],
        crs=tms.crs.to_epsg() or tms.crs.to_wkt(),
    )

    return bbox
