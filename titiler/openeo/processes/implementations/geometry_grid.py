"""Geometries on the pixel grid of a raster data cube (ADR 0014 §7.4).

Helpers for the processes that take a raster data cube and geometries:
`filter_spatial`, `apply_polygon` and `vector_to_raster`.
"""

from typing import Any, Dict, List, Optional, Tuple, Union

import numpy
from rasterio.crs import CRS
from rasterio.features import rasterize
from rasterio.transform import Affine, from_bounds

from ...errors import ProcessParameterInvalid
from .data_model import RasterStack
from .vector_model import VectorCube, is_empty_geometry

# The geometry types that cover an area: a pixel is covered if its center is
# in the geometry. Points and lines cover the pixels that they touch.
AREA_TYPES = ("Polygon", "MultiPolygon")

Grid = Tuple[int, int, Tuple[float, ...], CRS]


def stack_grid(stack: RasterStack, process: str) -> Grid:
    """Return the width, height, bounds and CRS of a raster data cube.

    The grid comes from the cube metadata, so the pixels are not read. Only
    when the metadata is not set, the first image is read.
    """
    if stack.width and stack.height and stack.bounds and stack.dst_crs:
        return stack.width, stack.height, tuple(stack.bounds), stack.dst_crs
    if not len(stack):
        raise ProcessParameterInvalid(f"{process}: the raster data cube has no data.")
    first = stack.first
    return first.width, first.height, tuple(first.bounds), first.crs


def grid_transform(grid: Grid) -> Affine:
    """Return the affine transform of a grid."""
    width, height, bounds, _ = grid
    return from_bounds(*bounds, width, height)


def as_vector_cube(
    geometries: Union[VectorCube, Dict[str, Any]], process: str
) -> VectorCube:
    """Return the geometries as a vector data cube.

    GeoJSON is still accepted (deprecated in openEO in favor of
    `load_geojson`). It is in EPSG:4326.
    """
    if isinstance(geometries, VectorCube):
        return geometries
    try:
        return VectorCube.from_geojson(geometries)
    except ValueError as error:
        raise ProcessParameterInvalid(
            f"{process}: the geometries must be a vector data cube or GeoJSON: {error}"
        ) from None


def geometries_on_grid(cube: VectorCube, grid: Grid) -> List[Optional[Dict[str, Any]]]:
    """Return the geometries of the cube in the CRS of the grid.

    Null and empty geometries are None.
    """
    return [
        None if is_empty_geometry(geometry) else geometry
        for geometry in cube._geometries_in(grid[3])
    ]


def geometry_pixels(cube: VectorCube, grid: Grid) -> numpy.ndarray:
    """Return the pixels that the geometries cover, as a boolean array.

    openEO `filter_spatial`: a polygon covers a pixel if the pixel center is in
    the polygon. A point covers the pixel that contains it, a line covers the
    pixels that it crosses.
    """
    width, height, _, _ = grid
    transform = grid_transform(grid)
    covered = numpy.zeros((height, width), dtype=bool)
    geometries = [g for g in geometries_on_grid(cube, grid) if g is not None]
    for all_touched in (False, True):
        shapes = [
            (geometry, 1)
            for geometry in geometries
            if (geometry["type"] in AREA_TYPES) != all_touched
        ]
        if shapes:
            covered |= rasterize(
                shapes,
                out_shape=(height, width),
                transform=transform,
                fill=0,
                dtype="uint8",
                all_touched=all_touched,
            ).astype(bool)
    return covered


def pixel_window(pixels: numpy.ndarray) -> Optional[Tuple[slice, slice]]:
    """Return the smallest row and column slices that hold all True pixels."""
    rows = numpy.flatnonzero(pixels.any(axis=1))
    cols = numpy.flatnonzero(pixels.any(axis=0))
    if not rows.size:
        return None
    return slice(int(rows[0]), int(rows[-1]) + 1), slice(
        int(cols[0]), int(cols[-1]) + 1
    )


def window_bounds(grid: Grid, window: Tuple[slice, slice]) -> Tuple[float, ...]:
    """Return the bounds (west, south, east, north) of a pixel window."""
    rows, cols = window
    transform = grid_transform(grid)
    west, north = transform * (cols.start, rows.start)
    east, south = transform * (cols.stop, rows.stop)
    return (west, south, east, north)
