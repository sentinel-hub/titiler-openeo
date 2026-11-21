"""titiler.openeo.processes Vector (ADR 0014)."""

from typing import Any, Dict, List, Optional, Tuple

import numpy
from rasterio.features import shapes
from rasterio.transform import from_bounds
from rasterio.warp import transform_geom
from rio_tiler.constants import WGS84_CRS

from ...errors import (
    ProcessParameterInvalid,
    VectorFeatureLimitExceeded,
    VectorizeEdgeLimitExceeded,
)
from ...settings import ProcessingSettings
from .data_model import RasterStack
from .reduce import REDUCED_TEMPORAL_SENTINEL
from .vector_model import GEOMETRY_DIMENSION, VectorCube

__all__ = ["load_geojson", "raster_to_vector", "vectorize"]

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
