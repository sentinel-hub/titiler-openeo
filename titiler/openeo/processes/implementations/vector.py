"""titiler.openeo.processes Vector (ADR 0014)."""

from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple, Union

import numpy
from rasterio.crs import CRS
from rasterio.errors import CRSError
from rasterio.features import rasterize, shapes
from rasterio.transform import from_bounds
from rasterio.warp import transform, transform_geom
from rio_tiler.constants import WGS84_CRS
from rio_tiler.models import ImageData

from ...errors import (
    DimensionNotAvailable,
    FeatureUnsupported,
    OutputLimitExceeded,
    ProcessParameterInvalid,
    UnitMismatch,
    VectorFeatureLimitExceeded,
    VectorizeEdgeLimitExceeded,
)
from ...settings import ProcessingSettings
from .data_model import RasterStack
from .reduce import REDUCED_TEMPORAL_SENTINEL
from .vector_model import GEOMETRY_DIMENSION, TEMPORAL_DIMENSION, VectorCube

__all__ = [
    "filter_vector",
    "load_geojson",
    "raster_to_vector",
    "vector_buffer",
    "vector_reproject",
    "vector_to_raster",
    "vectorize",
]

# The dimension that `load_geojson` makes from the listed properties.
PROPERTIES_DIMENSION = "properties"


def _zone_edges(values: numpy.ndarray, valid: numpy.ndarray) -> int:
    """Count the pixel edges between zones: the work of GDAL polygonize.

    An edge counts if it separates two valid pixels with different values, or
    a valid pixel and a masked one or the image border.
    """
    count = 0
    for axis in (0, 1):
        a = [slice(None), slice(None)]
        b = [slice(None), slice(None)]
        a[axis], b[axis] = slice(1, None), slice(None, -1)
        va, vb = valid[tuple(a)], valid[tuple(b)]
        differ = (values[tuple(a)] != values[tuple(b)]) & va & vb
        count += int(numpy.count_nonzero(differ | (va != vb)))
    # The border of the image: the valid pixels in the first and last rows
    # and columns.
    for edge in (valid[0], valid[-1], valid[:, 0], valid[:, -1]):
        count += int(numpy.count_nonzero(edge))
    return count


def raster_to_vector(data: RasterStack) -> VectorCube:
    """Convert a raster data cube with one band into polygons.

    For each date, the 4-connected pixels that have the same value become one
    polygon. Masked (no-data) pixels make no polygon.

    Args:
        data: A raster data cube with one band.

    Returns:
        A vector data cube with the `geometry` dimension. The value of each
        polygon is the value of its pixels. When the cube has more than one
        date, each polygon has its date in the property `t`. The geometries
        are in the CRS of the raster.

    Raises:
        ProcessParameterInvalid: If the cube has more than one band.
        VectorFeatureLimitExceeded: If there are more polygons than
            `max_vector_features`.
        VectorizeEdgeLimitExceeded: If the zones have more pixel edges than
            `max_vectorize_edges`, for all dates.

    """
    settings = ProcessingSettings()
    max_features = settings.max_vector_features
    edges = 0
    dates = [key for key in data.keys() if key != REDUCED_TEMPORAL_SENTINEL]
    with_dates = len(dates) > 1

    crs = None
    geometries: List[Optional[Dict[str, Any]]] = []
    properties: List[Dict[str, Any]] = []
    values: List[Any] = []
    dtype = None
    for key, img in data.items():
        if img.count != 1:
            raise ProcessParameterInvalid(
                f"raster_to_vector needs a raster data cube with one band, got "
                f"{img.count} bands ({', '.join(img.band_descriptions)}). Select "
                "one band with filter_bands or reduce the bands dimension first."
            )
        crs = crs or img.crs
        band = img.array[0]
        valid = ~numpy.ma.getmaskarray(band)
        source = band.data
        if source.dtype == bool:
            source = source.astype("uint8")
        dtype = dtype or source.dtype
        # GDAL makes all the polygons before the first one: check the work
        # before it starts.
        edges += _zone_edges(source, valid)
        if edges > settings.max_vectorize_edges:
            raise VectorizeEdgeLimitExceeded(
                "raster_to_vector", edges, settings.max_vectorize_edges
            )
        transform = from_bounds(*img.bounds, img.width, img.height)

        for geometry, value in shapes(
            source, mask=valid, connectivity=4, transform=transform
        ):
            if len(geometries) >= max_features:
                raise VectorFeatureLimitExceeded("raster_to_vector", max_features)
            if img.crs != crs:
                geometry = transform_geom(img.crs, crs, geometry)
            geometries.append(geometry)
            properties.append({"t": key} if with_dates else {})
            values.append(value)

    return VectorCube(
        geometries=geometries,
        crs=crs or WGS84_CRS,
        properties=properties,
        values=numpy.ma.masked_array(numpy.asarray(values, dtype=dtype or "float64")),
    )


def vectorize(data: RasterStack) -> VectorCube:
    """Convert a raster data cube with one band into polygons.

    Alias of `raster_to_vector`.
    """
    return raster_to_vector(data)


def _masked_values(rows: List[List[Any]], width: int) -> numpy.ma.MaskedArray:
    """Return the rows as a masked array of `width` columns. None is masked.

    Numbers keep a numeric dtype. Other values (text) give an object array.
    """
    cells = [list(row) + [None] * (width - len(row)) for row in rows]
    mask = numpy.array(
        [[value is None for value in row] for row in cells], dtype=bool
    ).reshape(len(cells), width)
    present = [value for row in cells for value in row if value is not None]
    if all(isinstance(value, (bool, int, float)) for value in present):
        dtype = numpy.asarray(present).dtype if present else numpy.dtype("float64")
        filled = [[0 if value is None else value for value in row] for row in cells]
        data = numpy.asarray(filled, dtype=dtype).reshape(len(cells), width)
    else:
        data = numpy.empty((len(cells), width), dtype=object)
        for i, row in enumerate(cells):
            data[i, :] = row
    return numpy.ma.masked_array(data, mask=mask)


def _property_rows(
    properties: List[Dict[str, Any]], names: List[str]
) -> Tuple[List[List[Any]], List[Any]]:
    """Take the listed properties out of each feature. Return the rows and labels.

    One property with array values: the labels are the array indices. Else
    the labels are the property names, and each value must be a scalar.
    """
    rows = [[feature.pop(name, None) for name in names] for feature in properties]
    if len(names) == 1 and any(isinstance(row[0], list) for row in rows):
        arrays = [
            row[0] if isinstance(row[0], list) else [] if row[0] is None else [row[0]]
            for row in rows
        ]
        width = max(len(array) for array in arrays)
        if any(isinstance(v, (list, dict)) for array in arrays for v in array):
            raise ProcessParameterInvalid(
                f"load_geojson: the arrays of the property '{names[0]}' must "
                "contain scalar values."
            )
        return arrays, list(range(width))
    if any(isinstance(value, (list, dict)) for row in rows for value in row):
        raise ProcessParameterInvalid(
            "load_geojson: a list or an object is accepted only when one "
            "property is given. With more than one property, each value must "
            "be a scalar."
        )
    return rows, list(names)


def load_geojson(data: Dict, properties: Optional[List[str]] = None) -> VectorCube:
    """Convert GeoJSON into a vector data cube.

    Args:
        data: A GeoJSON FeatureCollection, Feature or Geometry, in EPSG:4326
            (RFC 7946). GeometryCollection is not supported.
        properties: The properties to make the dimension `properties` from.
            One property with scalar values gives one label (its name). One
            property with array values gives one label for each array index.
            More than one property gives one label for each property. A
            missing value is no data.

    Returns:
        A vector data cube with the `geometry` dimension, and the
        `properties` dimension when properties are given. The other
        properties of the features are kept.

    Raises:
        ProcessParameterInvalid: If the GeoJSON is not valid, has a
            GeometryCollection, or the properties have unsupported values.
        VectorFeatureLimitExceeded: If there are more geometries than
            `max_vector_features`.

    """
    try:
        cube = VectorCube.from_geojson(data)
    except ValueError as error:
        raise ProcessParameterInvalid(
            f"load_geojson needs a GeoJSON object: {error}"
        ) from None
    if any(
        (geometry or {}).get("type") == "GeometryCollection"
        for geometry in cube.geometries
    ):
        raise ProcessParameterInvalid(
            "load_geojson does not support the GeoJSON type GeometryCollection."
        )
    max_features = ProcessingSettings().max_vector_features
    if len(cube) > max_features:
        raise VectorFeatureLimitExceeded("load_geojson", max_features)

    names = list(properties or [])
    if not names:
        return cube
    if len(set(names)) != len(names):
        raise ProcessParameterInvalid(
            "load_geojson: the properties must be unique, got " + ", ".join(names)
        )
    rows, labels = _property_rows(cube.properties, names)
    return VectorCube(
        geometries=cube.geometries,
        crs=cube.crs,
        properties=cube.properties,
        values=_masked_values(rows, len(labels)),
        dims=(GEOMETRY_DIMENSION, PROPERTIES_DIMENSION),
        labels={PROPERTIES_DIMENSION: labels},
    )


# The processes that need modules of the `vector` extra. Without them, the
# process registry does not list the process.
PROCESS_REQUIRES: Dict[str, Tuple[str, ...]] = {
    "vector_buffer": ("shapely",),
    "filter_vector": ("shapely",),
}

# The spatial relations of `filter_vector`. Each one is a shapely function.
FILTER_RELATIONS = (
    "intersects",
    "disjoint",
    "equals",
    "touches",
    "crosses",
    "overlaps",
    "contains",
    "within",
)


def _shapely(process: str):
    """Import shapely, which is in the `vector` extra."""
    try:
        import shapely
    except ImportError:  # pragma: no cover - the registry hides the process
        raise FeatureUnsupported(
            f"{process} needs the Python package extra `titiler-openeo[vector]`, "
            "which is not installed on this back-end."
        ) from None
    return shapely


def _parse_projection(projection: Union[int, str]) -> CRS:
    """Return the CRS of an EPSG code or a WKT2 string."""
    try:
        if isinstance(projection, int) and not isinstance(projection, bool):
            return CRS.from_epsg(projection)
        if isinstance(projection, str):
            return CRS.from_user_input(projection)
    except CRSError as error:
        raise ProcessParameterInvalid(
            f"vector_reproject: the projection {projection!r} is not valid: {error}"
        ) from None
    raise ProcessParameterInvalid(
        "vector_reproject: the projection must be an EPSG code or a WKT2 string."
    )


def vector_reproject(
    data: VectorCube,
    projection: Union[int, str],
    dimension: Optional[str] = None,
) -> VectorCube:
    """Reproject the geometries of a vector data cube.

    Args:
        data: A vector data cube.
        projection: The CRS to reproject to: an EPSG code or a WKT2 string.
        dimension: The geometry dimension. A vector data cube has one, named
            `geometry`.

    Returns:
        The vector data cube with the geometries in the new CRS. The values
        and the properties do not change.

    Raises:
        DimensionNotAvailable: If `dimension` is not the geometry dimension.
        ProcessParameterInvalid: If the projection is not valid.

    """
    if dimension is not None and dimension != GEOMETRY_DIMENSION:
        raise DimensionNotAvailable(dimension)
    crs = _parse_projection(projection)
    return VectorCube(
        geometries=list(data._geometries_in(crs)),
        crs=crs,
        properties=[dict(p) for p in data.properties],
        values=data.values,
        dims=data.dims,
        labels=data.labels,
    )


def _utm_epsg(cube: VectorCube, shapely: Any) -> Optional[int]:
    """Return the EPSG code of the WGS 84 UTM zone at the center of the cube."""
    from shapely.geometry import shape

    geometries = [shape(g) for g in cube.geometries if g]
    if not geometries:
        return None
    minx, miny, maxx, maxy = shapely.total_bounds(geometries)
    xs, ys = [(minx + maxx) / 2], [(miny + maxy) / 2]
    if cube.crs != WGS84_CRS:
        xs, ys = transform(cube.crs, WGS84_CRS, xs, ys)
    zone = int((xs[0] + 180) // 6) % 60 + 1
    return (32600 if ys[0] >= 0 else 32700) + zone


def _check_metres(cube: VectorCube, shapely: Any) -> None:
    """Raise UnitMismatch if the unit of the CRS of the cube is not the metre."""
    crs = cube.crs
    if not crs.is_geographic and crs.linear_units_factor == ("metre", 1.0):
        return
    unit = "degrees" if crs.is_geographic else crs.linear_units
    hint = "Reproject the cube first with vector_reproject"
    epsg = _utm_epsg(cube, shapely) if crs.is_geographic else None
    if epsg:
        hint += f", for example to EPSG:{epsg} (the UTM zone of the data)"
    raise UnitMismatch(
        f"vector_buffer: the distance is in metres, but the unit of the "
        f"coordinate reference system of the geometries ({crs.to_string()}) "
        f"is {unit}. {hint}."
    )


def vector_buffer(geometries: VectorCube, distance: float) -> VectorCube:
    """Buffer the geometries of a vector data cube by a distance in metres.

    Args:
        geometries: A vector data cube, in a CRS whose unit is the metre.
        distance: The distance in metres, not 0. A positive distance expands
            the geometries; a negative distance shrinks them, and can make
            them empty.

    Returns:
        The vector data cube with polygons. The values and the properties
        do not change. An empty geometry stays empty.

    Raises:
        ProcessParameterInvalid: If the distance is 0.
        UnitMismatch: If the unit of the CRS is not the metre.

    """
    shapely = _shapely("vector_buffer")
    from shapely.geometry import mapping, shape

    if distance == 0:
        raise ProcessParameterInvalid("vector_buffer: the distance must not be 0.")
    _check_metres(geometries, shapely)
    buffered = [
        None if g is None else mapping(shapely.buffer(shape(g), distance))
        for g in geometries.geometries
    ]
    return VectorCube(
        geometries=buffered,
        crs=geometries.crs,
        properties=[dict(p) for p in geometries.properties],
        values=geometries.values,
        dims=geometries.dims,
        labels=geometries.labels,
    )


def filter_vector(
    data: VectorCube, geometries: VectorCube, relation: str = "intersects"
) -> VectorCube:
    """Keep the geometries of a vector data cube that relate to other geometries.

    A geometry of `data` (candidate) is kept if
    `<relation>(candidate, union of geometries)` is true. For example,
    `within` keeps the candidates that are inside the base geometries. The
    relations are those of the OGC Simple Features standard.

    Args:
        data: A vector data cube with the candidate geometries.
        geometries: A vector data cube with the base geometries. Their union is
            used. They are reprojected to the CRS of `data`.
        relation: One of intersects, disjoint, equals, touches, crosses,
            overlaps, contains, within.

    Returns:
        The vector data cube with the candidates that are kept, in their
        order. Empty and null candidate geometries are removed.

    Raises:
        ProcessParameterInvalid: If the relation is not known.

    """
    shapely = _shapely("filter_vector")
    from shapely.geometry import shape

    if relation not in FILTER_RELATIONS:
        raise ProcessParameterInvalid(
            f"filter_vector: the relation '{relation}' is not supported. "
            f"Supported relations: {', '.join(FILTER_RELATIONS)}."
        )
    base = shapely.union_all(
        [shape(g) for g in geometries._geometries_in(data.crs) if g]
    )
    shapely.prepare(base)
    candidates = numpy.array(
        [shapely.Polygon() if g is None else shape(g) for g in data.geometries],
        dtype=object,
    )
    keep = getattr(shapely, relation)(candidates, base) & ~shapely.is_empty(candidates)
    return data.select(numpy.flatnonzero(keep))


# The band name of `vector_to_raster` for a cube without a bands dimension.
VALUE_BAND = "value"


def _target_grid(target: RasterStack) -> Tuple[int, int, Tuple[float, ...], CRS]:
    """Return the width, height, bounds and CRS of a raster data cube.

    The grid comes from the cube metadata, so the pixels are not read. Only
    when the metadata is not set, the first image is read.
    """
    if target.width and target.height and target.bounds and target.dst_crs:
        return target.width, target.height, tuple(target.bounds), target.dst_crs
    if not len(target):
        raise ProcessParameterInvalid(
            "vector_to_raster: the target raster data cube has no data."
        )
    first = target.first
    return first.width, first.height, tuple(first.bounds), first.crs


def _numeric_values(cube: VectorCube) -> numpy.ndarray:
    """Return the values of the cube with a numeric dtype. Masked cells are 0."""
    assert cube.values is not None
    data = cube.values.data
    if data.dtype == bool:
        return data.astype("uint8")
    if data.dtype.kind in "iuf":
        return data
    mask = numpy.ma.getmaskarray(cube.values)
    cells = data[~mask].tolist()
    sample = numpy.asarray(cells) if cells else numpy.zeros(0, dtype="float64")
    if sample.dtype.kind not in "biuf":
        raise ProcessParameterInvalid(
            "vector_to_raster: the values of the vector data cube must be "
            f"numbers, got {sample.dtype.kind!r} values (for example "
            f"{cells[0]!r})."
        )
    out = numpy.zeros(
        data.shape, dtype="uint8" if sample.dtype == bool else sample.dtype
    )
    out[~mask] = sample
    return out


def _date_key(label: Any) -> datetime:
    """Return a label of the `t` dimension as a datetime."""
    if isinstance(label, datetime):
        return label
    if isinstance(label, date):
        return datetime(label.year, label.month, label.day)
    if isinstance(label, str):
        try:
            return datetime.fromisoformat(label.replace("Z", "+00:00"))
        except ValueError:
            pass
    raise ProcessParameterInvalid(
        f"vector_to_raster: the label {label!r} of the dimension "
        f"'{TEMPORAL_DIMENSION}' is not a date."
    )


def _is_empty(geometry: Optional[Dict[str, Any]]) -> bool:
    """Return True for a null geometry or a geometry without coordinates."""
    return not geometry or not (
        geometry.get("coordinates") or geometry.get("geometries")
    )


def vector_to_raster(data: VectorCube, target: RasterStack) -> RasterStack:
    """Convert a vector data cube into a raster data cube on the grid of `target`.

    Each pixel gets the values of the last geometry that covers it, in the
    order of the cube. A pixel is covered by a polygon if its center is in
    the polygon. Points and lines cover the pixels on their path. A pixel
    that no geometry covers has no data, and so does a pixel whose geometry
    has no data.

    Args:
        data: A vector data cube with values. Its dimensions are `geometry`,
            optionally `t`, and at most one other dimension, which becomes
            the bands (for example `bands`, or `properties` from
            `load_geojson`).
        target: A raster data cube. The result has its CRS, extent and
            resolution. Its pixels are not read.

    Returns:
        A raster data cube with one band for each label of the other
        dimension (one band `value` if there is none) and one date for each
        label of `t`. Without `t`, the result has no temporal dimension.

    Raises:
        ProcessParameterInvalid: If the cube has no values, values that are
            not numbers, or more than one dimension other than `t`.
        OutputLimitExceeded: If the result has more pixels than
            `max_pixels`.

    """
    if data.values is None:
        raise ProcessParameterInvalid(
            "vector_to_raster: the vector data cube has no values to "
            "rasterize. Use load_geojson with `properties`, or "
            "aggregate_spatial, to make a vector data cube with values."
        )
    other = [dim for dim in data.dims[1:] if dim != TEMPORAL_DIMENSION]
    if len(other) > 1:
        raise ProcessParameterInvalid(
            f"vector_to_raster: the vector data cube has the dimensions "
            f"{list(data.dims)}. Only one dimension other than "
            f"'{GEOMETRY_DIMENSION}' and '{TEMPORAL_DIMENSION}' can become "
            f"the bands: reduce or filter the dimensions {other[1:]} first."
        )

    # The values as (geometry, date, band).
    values = _numeric_values(data)
    mask = numpy.ma.getmaskarray(data.values)
    if TEMPORAL_DIMENSION in data.dims:
        axis = data.dims.index(TEMPORAL_DIMENSION)
        values = numpy.moveaxis(values, axis, 1)
        mask = numpy.moveaxis(mask, axis, 1)
        keys = [_date_key(label) for label in data.labels[TEMPORAL_DIMENSION]]
    else:
        values, mask = values[:, numpy.newaxis], mask[:, numpy.newaxis]
        keys = [REDUCED_TEMPORAL_SENTINEL]
    band_names = (
        [str(label) for label in data.labels[other[0]]] if other else [VALUE_BAND]
    )
    if not keys or not band_names:
        raise ProcessParameterInvalid(
            f"vector_to_raster: the vector data cube has a dimension without "
            f"labels ({list(data.dims)}), so the raster would be empty."
        )
    values = values.reshape(len(data), len(keys), len(band_names))
    mask = mask.reshape(values.shape)
    if not len(data):
        # No geometry: every pixel has no data.
        values = numpy.zeros((1,) + values.shape[1:], dtype=values.dtype)
        mask = numpy.ones(values.shape, dtype=bool)

    width, height, bounds, crs = _target_grid(target)
    max_pixels = ProcessingSettings().max_pixels
    if width * height * len(keys) * len(band_names) > max_pixels:
        raise OutputLimitExceeded(
            width,
            height,
            max_pixels,
            items_count=len(keys),
            bands_count=len(band_names),
        )

    # One rasterization for all dates and bands: each pixel gets the index
    # (+1) of the last geometry that covers it, 0 if none.
    grid_transform = from_bounds(*bounds, width, height)
    burns = [
        (geometry, i + 1)
        for i, geometry in enumerate(data._geometries_in(crs))
        if not _is_empty(geometry)
    ]
    index = rasterize(
        burns,
        out_shape=(height, width),
        transform=grid_transform,
        fill=0,
        dtype="int32",
    )
    covered = index > 0
    index = numpy.maximum(index - 1, 0)

    images = {}
    for d, key in enumerate(keys):
        # (height, width, band) -> (band, height, width)
        array = numpy.moveaxis(values[index, d], -1, 0)
        no_data = numpy.moveaxis(mask[index, d], -1, 0) | ~covered
        images[key] = ImageData(
            numpy.ma.MaskedArray(array, mask=no_data),
            crs=crs,
            bounds=bounds,
            band_descriptions=band_names,
        )
    return RasterStack.from_images(images)
