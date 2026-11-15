"""Generic openEO processes on vector data cubes (ADR 0014 §7.2).

The openEO processes that take any data cube (`apply`, `reduce_dimension`,
`filter_labels`...) work on the dimensions of a vector data cube other than
`geometry`, with numpy on `VectorCube.values`. Each raster process calls the
function here when its data is a `VectorCube`.

This module does not import the raster process modules, so that they can
import it.
"""

import dataclasses
from datetime import datetime
from typing import Any, Callable, Dict, Iterator, List, Optional, Sequence, Set, Tuple

import numpy
from rasterio.crs import CRS

from ...errors import (
    DimensionNotAvailable,
    FeatureUnsupported,
    IncompatibleGeometryTypes,
    OverlapResolverMissing,
    ProcessParameterInvalid,
    VectorFeatureLimitExceeded,
)
from ...settings import ProcessingSettings
from .callbacks import call_overlap_resolver, call_reducer
from .vector_model import (
    GEOMETRY_DIMENSION,
    TEMPORAL_DIMENSION,
    VectorCube,
    is_empty_geometry,
)

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


# The base geometry type of each GeoJSON type (openEO merge_cubes): a type
# can be merged with its multi type.
_BASE_GEOMETRY_TYPES = {
    "Point": "Point",
    "MultiPoint": "Point",
    "LineString": "LineString",
    "MultiLineString": "LineString",
    "Polygon": "Polygon",
    "MultiPolygon": "Polygon",
}


def _base_types(cube: VectorCube) -> Set[str]:
    """Return the base geometry types of the cube. Null and empty are ignored."""
    return {
        _BASE_GEOMETRY_TYPES.get(geometry["type"], geometry["type"])
        for geometry in cube.geometries
        if geometry is not None and not is_empty_geometry(geometry)
    }


def _match_geometries(
    geometries1: List[Optional[Dict[str, Any]]],
    geometries2: List[Optional[Dict[str, Any]]],
) -> List[Optional[int]]:
    """Return, for each geometry of the second list, an equal one of the first.

    Equality is the OGC Simple Features equality (`shapely.equals`). Each
    geometry of the first list matches one geometry only, in order: so a
    cube merged with itself matches each geometry with itself. Null and empty
    geometries match nothing.
    """
    try:
        import shapely
        from shapely.geometry import shape
    except ImportError:  # pragma: no cover - the Docker image has the extra
        raise FeatureUnsupported(
            "merge_cubes needs the Python package extra `titiler-openeo[vector]` "
            "to compare the geometries of two vector data cubes."
        ) from None

    def _valid(geometries: List[Optional[Dict[str, Any]]]) -> Tuple[numpy.ndarray, Any]:
        index = [i for i, g in enumerate(geometries) if not is_empty_geometry(g)]
        array = numpy.array([shape(geometries[i]) for i in index], dtype=object)
        return numpy.asarray(index, dtype=numpy.intp), array

    index1, shapes1 = _valid(geometries1)
    index2, shapes2 = _valid(geometries2)
    matches: List[Optional[int]] = [None] * len(geometries2)
    if not len(index1) or not len(index2):
        return matches

    # Candidates by bounding box, then the exact test.
    pairs = shapely.STRtree(shapes1).query(shapes2)
    equal = shapely.equals(shapes2[pairs[0]], shapes1[pairs[1]])
    pairs = pairs[:, equal]
    pairs = pairs[:, numpy.lexsort((pairs[1], pairs[0]))]

    used: Set[int] = set()
    for i, j in zip(pairs[0].tolist(), pairs[1].tolist()):
        first = int(index1[j])
        target = int(index2[i])
        if matches[target] is None and first not in used:
            matches[target] = first
            used.add(first)
    return matches


def _merged_labels(dimension: str, labels1: List[Any], labels2: List[Any]) -> List[Any]:
    """Join the labels of a dimension (openEO merge_cubes).

    The temporal labels stay sorted. The other labels keep the order of
    `cube1`, then the new labels of `cube2`.
    """
    joined = list(labels1) + [label for label in labels2 if label not in labels1]
    if dimension == TEMPORAL_DIMENSION and all(
        isinstance(label, datetime) for label in joined
    ):
        joined.sort()
    return joined


def _spread(
    cube: VectorCube,
    rows: numpy.ndarray,
    size: int,
    dims: Tuple[str, ...],
    labels: Dict[str, List[Any]],
    dtype: Any,
) -> Tuple[numpy.ndarray, numpy.ndarray, numpy.ndarray]:
    """Put the values of a cube on the grid of the merged cube.

    Args:
        cube: One of the two cubes.
        rows: The row of the merged cube for each geometry of the cube.
        size: The number of geometries of the merged cube.
        dims: The dimensions of the merged cube.
        labels: The labels of the merged cube.
        dtype: The dtype of the merged values.

    Returns the data, the mask and the cells that the cube covers. A
    dimension that the cube does not have is repeated (openEO merge_cubes,
    example 4).
    """
    shape = (size,) + tuple(len(labels[dim]) for dim in dims[1:])
    data = numpy.zeros(shape, dtype=dtype)
    mask = numpy.ones(shape, dtype=bool)
    covered = numpy.zeros(shape, dtype=bool)
    values = cube.numeric_values()
    if values is None:
        values = cube.values
    if values is None:
        return data, mask, covered

    # The axes of the cube in the order of `dims`, then a new axis of length 1
    # for each dimension that the cube does not have.
    own = [dim for dim in dims if dim in cube.dims]
    values = numpy.ma.MaskedArray(
        numpy.transpose(values.data, [cube.dims.index(dim) for dim in own]),
        mask=numpy.transpose(
            numpy.ma.getmaskarray(values), [cube.dims.index(dim) for dim in own]
        ),
    )
    positions: List[numpy.ndarray] = [rows]
    for dim in dims[1:]:
        if dim in cube.dims:
            positions.append(
                numpy.asarray(
                    [labels[dim].index(label) for label in cube.labels[dim]],
                    dtype=numpy.intp,
                )
            )
        else:
            values = numpy.ma.expand_dims(values, dims.index(dim))
            positions.append(numpy.arange(len(labels[dim]), dtype=numpy.intp))
    target = numpy.ix_(*positions)
    region = tuple(len(p) for p in positions)
    data[target] = numpy.broadcast_to(values.data, region)
    mask[target] = numpy.broadcast_to(numpy.ma.getmaskarray(values), region)
    covered[target] = True
    return data, mask, covered


def merge_vector_cubes(
    cube1: VectorCube,
    cube2: VectorCube,
    overlap_resolver: Optional[Callable] = None,
    context: Optional[Any] = None,
) -> VectorCube:
    """Merge two vector data cubes (openEO merge_cubes).

    - The geometries of `cube2` are reprojected to the CRS of `cube1`. An
      equal geometry (OGC equality) is the same label of the `geometry`
      dimension; the others are appended.
    - The other dimensions: the labels of both cubes. A cube without a
      dimension is repeated along it.
    - A cell that both cubes have needs `overlap_resolver`, which is called
      once with all such cells (`x` from `cube1`, `y` from `cube2`).
    - The properties of an equal geometry: those of `cube1`, and the keys of
      `cube2` that `cube1` does not have.

    Raises:
        IncompatibleGeometryTypes: For different base geometry types.
        ProcessParameterInvalid: If each cube has a dimension that the other
            does not have.
        OverlapResolverMissing: For overlapping cells without a resolver.
        VectorFeatureLimitExceeded: For more geometries than the limit.

    """
    types1, types2 = _base_types(cube1), _base_types(cube2)
    if types1 and types2 and types1 != types2:
        raise IncompatibleGeometryTypes(
            "merge_cubes: the geometry types are not compatible and can't be "
            f"merged: {sorted(types1)} and {sorted(types2)}. A type can be "
            "merged with its multi type only."
        )
    dims1, dims2 = set(cube1.dims), set(cube2.dims)
    if not (dims1 <= dims2 or dims2 <= dims1):
        raise ProcessParameterInvalid(
            f"merge_cubes: the vector data cubes are not compatible: they have "
            f"the dimensions {list(cube1.dims)} and {list(cube2.dims)}. One cube "
            "must have all the dimensions of the other."
        )

    geometries2 = cube2._geometries_in(cube1.crs)
    matches = _match_geometries(cube1.geometries, geometries2)
    geometries = list(cube1.geometries)
    properties = [dict(p) for p in cube1.properties]
    rows2 = []
    for i, match in enumerate(matches):
        if match is None:
            rows2.append(len(geometries))
            geometries.append(geometries2[i])
            properties.append(dict(cube2.properties[i]))
        else:
            rows2.append(match)
            properties[match] = {**cube2.properties[i], **properties[match]}

    max_features = ProcessingSettings().max_vector_features
    if len(geometries) > max_features:
        raise VectorFeatureLimitExceeded("merge_cubes", max_features)

    dims = cube1.dims + tuple(dim for dim in cube2.dims if dim not in cube1.dims)
    if cube1.values is None and cube2.values is None:
        return VectorCube(geometries=geometries, crs=cube1.crs, properties=properties)

    labels: Dict[str, Any] = {
        dim: _merged_labels(dim, cube1.labels.get(dim, []), cube2.labels.get(dim, []))
        for dim in dims[1:]
    }
    numeric = [
        cube.numeric_values() for cube in (cube1, cube2) if cube.values is not None
    ]
    dtype = (
        numpy.result_type(*(values.dtype for values in numeric))  # type: ignore[union-attr]
        if all(values is not None for values in numeric)
        else object
    )
    size = len(geometries)
    data1, mask1, covered1 = _spread(
        cube1, numpy.arange(len(cube1), dtype=numpy.intp), size, dims, labels, dtype
    )
    data2, mask2, covered2 = _spread(
        cube2, numpy.asarray(rows2, dtype=numpy.intp), size, dims, labels, dtype
    )

    only2 = covered2 & ~covered1
    data = numpy.where(only2, data2, data1)
    mask = numpy.where(only2, mask2, mask1)
    overlap = covered1 & covered2
    if overlap.any():
        if overlap_resolver is None:
            raise OverlapResolverMissing()
        x = numpy.ma.MaskedArray(data1[overlap], mask=mask1[overlap])
        y = numpy.ma.MaskedArray(data2[overlap], mask=mask2[overlap])
        resolved = numpy.ma.asanyarray(
            call_overlap_resolver(overlap_resolver, x, y, context)
        )
        resolved = numpy.ma.MaskedArray(
            numpy.broadcast_to(resolved.data, x.shape),
            mask=numpy.broadcast_to(numpy.ma.getmaskarray(resolved), x.shape),
        )
        if data.dtype != object:
            data = data.astype(numpy.result_type(data.dtype, resolved.dtype))
        data[overlap] = resolved.data
        mask[overlap] = numpy.ma.getmaskarray(resolved)

    return VectorCube(
        geometries=geometries,
        crs=cube1.crs,
        properties=properties,
        values=numpy.ma.MaskedArray(data, mask=mask),
        dims=dims,
        labels=labels,
    )
