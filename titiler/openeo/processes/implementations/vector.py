"""titiler.openeo.processes Vector (ADR 0014)."""

from typing import Any, Dict, List, Optional

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
from .vector_model import VectorCube

__all__ = ["raster_to_vector", "vectorize"]


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
