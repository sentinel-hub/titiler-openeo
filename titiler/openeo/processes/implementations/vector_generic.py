"""Generic openEO processes on vector data cubes (ADR 0014 §7.2).

The openEO processes that take any data cube (`apply`, `reduce_dimension`,
`filter_labels`...) work on the dimensions of a vector data cube other than
`geometry`, with numpy on `VectorCube.values`. Each raster process calls the
function here when its data is a `VectorCube`.

This module does not import the raster process modules, so that they can
import it.
"""

import dataclasses
from typing import Any, Callable, Dict, Iterator, List, Optional, Sequence

import numpy
from rasterio.crs import CRS

from ...errors import DimensionNotAvailable, ProcessParameterInvalid
from .callbacks import call_reducer
from .vector_model import GEOMETRY_DIMENSION, TEMPORAL_DIMENSION, VectorCube

# The other names of the `t` and `bands` dimensions, as for raster data cubes.
TEMPORAL_ALIASES = ("t", "temporal", "time")
SPECTRAL_ALIASES = ("bands", "spectral")
BANDS_DIMENSION = "bands"


def vector_dimension(
    cube: VectorCube, dimension: str, process: str, geometry: bool = False
) -> str:
    """Return the name of a dimension of the cube.

    `temporal` and `time` name the `t` dimension, `spectral` names `bands`.

    Args:
        cube: The vector data cube.
        dimension: The name that the process graph gives.
        process: The process name, for the error message.
        geometry: True if the process accepts the `geometry` dimension.

    Raises:
        DimensionNotAvailable: If the cube has no such dimension.
        ProcessParameterInvalid: For `geometry`, if the process does not
            accept it.

    """
    name = dimension
    if name not in cube.dims:
        lower = dimension.lower()
        if lower in TEMPORAL_ALIASES:
            name = TEMPORAL_DIMENSION
        elif lower in SPECTRAL_ALIASES:
            name = BANDS_DIMENSION
        if name not in cube.dims:
            raise DimensionNotAvailable(dimension)
    if name == GEOMETRY_DIMENSION and not geometry:
        raise ProcessParameterInvalid(
            f"{process} does not work on the '{GEOMETRY_DIMENSION}' dimension of "
            "a vector data cube. Use filter_vector or filter_bbox to select "
            "geometries."
        )
    return name


def vector_values(cube: VectorCube, process: str) -> numpy.ma.MaskedArray:
    """Return the values of the cube, numeric if all of them are numbers.

    Raises:
        ProcessParameterInvalid: If the cube has geometries only.

    """
    if cube.values is None:
        raise ProcessParameterInvalid(
            f"{process}: the vector data cube has no values, only geometries. "
            "Use load_geojson with `properties`, or aggregate_spatial, to make "
            "a vector data cube with values."
        )
    numeric = cube.numeric_values()
    return cube.values if numeric is None else numeric


def select_labels(
    cube: VectorCube, dimension: str, indices: Sequence[int]
) -> VectorCube:
    """Return the cube with the labels of `dimension` at `indices` only."""
    axis = cube.dims.index(dimension)
    labels = dict(cube.labels)
    labels[dimension] = [cube.labels[dimension][i] for i in indices]
    assert cube.values is not None
    values = cube.values.take(numpy.asarray(indices, dtype=numpy.intp), axis=axis)
    return dataclasses.replace(cube, values=values, labels=labels)


def with_labels(cube: VectorCube, dimension: str, labels: List[Any]) -> VectorCube:
    """Return the cube with new labels for `dimension`. The values do not change."""
    new = dict(cube.labels)
    new[dimension] = list(labels)
    return dataclasses.replace(cube, labels=new)


def reduce_vector(
    cube: VectorCube,
    dimension: str,
    reducer: Callable,
    context: Optional[Any] = None,
    named_parameters: Optional[Dict[str, Any]] = None,
) -> VectorCube:
    """Reduce a dimension of a vector data cube.

    The reducer is called once, with the values of all the geometries. The
    reduced dimension is axis 0, as for the bands of a raster data cube.

    Raises:
        ProcessParameterInvalid: If the reducer does not remove axis 0.

    """
    name = vector_dimension(cube, dimension, "reduce_dimension")
    values = vector_values(cube, "reduce_dimension")
    axis = cube.dims.index(name)
    data = numpy.ma.MaskedArray(
        numpy.moveaxis(values.data, axis, 0),
        mask=numpy.moveaxis(numpy.ma.getmaskarray(values), axis, 0),
    )
    result = numpy.ma.asanyarray(call_reducer(reducer, data, context, named_parameters))
    if result.shape != data.shape[1:]:
        raise ProcessParameterInvalid(
            f"reduce_dimension: the reducer must return one value for each "
            f"geometry and each label of the other dimensions (shape "
            f"{data.shape[1:]}), got the shape {result.shape}."
        )
    labels = {dim: labels for dim, labels in cube.labels.items() if dim != name}
    return dataclasses.replace(
        cube,
        values=result,
        dims=tuple(dim for dim in cube.dims if dim != name),
        labels=labels,
    )


def apply_vector(
    cube: VectorCube,
    process: Callable,
    context: Optional[Any] = None,
    named_parameters: Optional[Dict[str, Any]] = None,
) -> VectorCube:
    """Apply a unary process to each value of a vector data cube.

    The process is called once, with all the values (see apply.py).
    """
    values = vector_values(cube, "apply")
    result = numpy.ma.asanyarray(
        process(
            values,
            positional_parameters={"x": 0},
            named_parameters={
                **(named_parameters or {}),
                "x": values,
                "context": context,
            },
        )
    )
    if result.shape != values.shape:
        try:
            result = numpy.ma.MaskedArray(
                numpy.broadcast_to(result.data, values.shape),
                mask=numpy.broadcast_to(numpy.ma.getmaskarray(result), values.shape),
            )
        except ValueError:
            raise ProcessParameterInvalid(
                f"apply: the process must return one value for each value of "
                f"the vector data cube (shape {values.shape}), got the shape "
                f"{result.shape}."
            ) from None
    return dataclasses.replace(cube, values=result)


def _positions(coordinates: Any) -> Iterator[Sequence[float]]:
    """Yield the positions of nested GeoJSON coordinates."""
    if coordinates and isinstance(coordinates[0], (int, float)):
        yield coordinates
        return
    for item in coordinates or ():
        yield from _positions(item)


def _geometry_positions(geometry: Dict[str, Any]) -> Iterator[Sequence[float]]:
    """Yield the positions of a GeoJSON geometry (also a GeometryCollection)."""
    if geometry.get("type") == "GeometryCollection":
        for part in geometry.get("geometries") or ():
            yield from _geometry_positions(part)
        return
    yield from _positions(geometry.get("coordinates"))


def filter_vector_bbox(
    cube: VectorCube,
    west: float,
    south: float,
    east: float,
    north: float,
    crs: CRS,
) -> VectorCube:
    """Keep the geometries that are fully within a bounding box.

    The geometries are reprojected to the CRS of the box. A geometry is in an
    axis-aligned box if and only if its bounds are in the box, so this needs
    no geometry library. Null and empty geometries are removed.
    """
    keep = []
    for i, geometry in enumerate(cube._geometries_in(crs)):
        if not geometry:
            continue
        points = numpy.asarray(
            [position[:2] for position in _geometry_positions(geometry)],
            dtype="float64",
        )
        if not points.size:
            continue
        x, y = points[:, 0], points[:, 1]
        if (
            x.min() >= west
            and x.max() <= east
            and y.min() >= south
            and y.max() <= north
        ):
            keep.append(i)
    return cube.select(keep)
