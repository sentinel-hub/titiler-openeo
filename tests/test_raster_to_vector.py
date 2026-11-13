"""Tests for raster_to_vector and vectorize (ADR 0014 §2.3)."""

import json
from datetime import datetime
from typing import Optional

import numpy as np
import pytest
from rasterio.crs import CRS
from rio_tiler.models import ImageData
from shapely.geometry import shape

from titiler.openeo.errors import ProcessParameterInvalid, VectorFeatureLimitExceeded
from titiler.openeo.processes.implementations.core import process
from titiler.openeo.processes.implementations.data_model import RasterStack
from titiler.openeo.processes.implementations.formats import encode
from titiler.openeo.processes.implementations.reduce import REDUCED_TEMPORAL_SENTINEL
from titiler.openeo.processes.implementations.spatial import aggregate_spatial
from titiler.openeo.processes.implementations.vector import raster_to_vector, vectorize
from titiler.openeo.processes.implementations.vector_model import VectorCube

D1, D2 = datetime(2023, 1, 5), datetime(2023, 2, 1)

# Two zones: value 1 in the two left columns, value 2 in the two right ones.
ZONES = np.array([[1, 1, 2, 2]] * 4, dtype="uint8")


def _img(
    array: np.ndarray,
    mask: Optional[np.ndarray] = None,
    crs: CRS = CRS.from_epsg(4326),
    bounds=(0, 0, 4, 4),
    bands=None,
) -> ImageData:
    data = array if array.ndim == 3 else array[np.newaxis]
    return ImageData(
        np.ma.array(data, mask=np.zeros(data.shape, bool) if mask is None else mask),
        band_descriptions=bands or [f"b{i + 1}" for i in range(data.shape[0])],
        crs=crs,
        bounds=bounds,
    )


def _by_value(cube: VectorCube) -> dict:
    return {
        cube.values[i].item(): shape(geometry)
        for i, geometry in enumerate(cube.geometries)
    }


def test_zones_become_polygons():
    """Each 4-connected zone of equal value is one polygon, with that value."""
    cube = raster_to_vector(RasterStack.from_images({D1: _img(ZONES)}))

    assert isinstance(cube, VectorCube)
    assert cube.dims == ("geometry",)
    assert cube.crs == CRS.from_epsg(4326)
    assert cube.values.dtype == np.uint8
    polygons = _by_value(cube)
    assert set(polygons) == {1, 2}
    assert polygons[1].equals(
        shape(
            {
                "type": "Polygon",
                "coordinates": [[(0, 0), (2, 0), (2, 4), (0, 4), (0, 0)]],
            }
        )
    )
    assert polygons[2].area == 8
    # One date: no `t` property.
    assert cube.properties == [{}, {}]


def test_masked_pixels_make_no_polygon():
    """A masked pixel is a hole in its zone, not a polygon."""
    mask = np.zeros((1, 4, 4), bool)
    mask[0, 1, 2] = True
    cube = raster_to_vector(RasterStack.from_images({D1: _img(ZONES, mask=mask)}))

    polygons = _by_value(cube)
    assert len(cube) == 2
    assert polygons[2].area == 7
    assert sum(p.area for p in polygons.values()) == 15


def test_diagonal_pixels_are_not_connected():
    """Connectivity is 4: pixels that touch only at a corner are two polygons."""
    checker = np.array([[1, 0], [0, 1]], dtype="uint8")
    cube = raster_to_vector(
        RasterStack.from_images({D1: _img(checker, bounds=(0, 0, 2, 2))})
    )
    assert sorted(cube.values.tolist()) == [0, 0, 1, 1]


def test_each_date_has_its_polygons_and_t():
    """With more than one date, each polygon has its date in `t`."""
    later = ZONES.copy()
    later[:, :] = 5
    cube = raster_to_vector(RasterStack.from_images({D1: _img(ZONES), D2: _img(later)}))

    assert len(cube) == 3
    assert [p["t"] for p in cube.properties] == [D1, D1, D2]
    assert cube.values.tolist()[-1] == 5
    document = json.loads(encode(cube, "GeoJSON").data)
    assert document["features"][2]["properties"] == {
        "t": "2023-02-01T00:00:00",
        "value": 5,
    }


def test_reduced_time_dimension_has_no_t():
    """After reduce_dimension over t, the sentinel date is not a real date."""
    cube = raster_to_vector(
        RasterStack.from_images({REDUCED_TEMPORAL_SENTINEL: _img(ZONES)})
    )
    assert cube.properties == [{}, {}]


def test_more_than_one_band_is_rejected():
    """The cube must have one band."""
    two_bands = np.stack([ZONES, ZONES])
    with pytest.raises(ProcessParameterInvalid, match="one band, got 2 bands"):
        raster_to_vector(
            RasterStack.from_images({D1: _img(two_bands, bands=["B03", "B04"])})
        )


def test_feature_limit(monkeypatch):
    """More polygons than max_vector_features gives OutputLimitExceeded."""
    monkeypatch.setenv("TITILER_OPENEO_PROCESSING_MAX_VECTOR_FEATURES", "3")
    checker = (np.indices((4, 4)).sum(axis=0) % 2).astype("uint8")
    with pytest.raises(VectorFeatureLimitExceeded) as error:
        raster_to_vector(RasterStack.from_images({D1: _img(checker)}))
    assert error.value.code == "OutputLimitExceeded"
    assert error.value.status_code == 400


@pytest.mark.parametrize("dtype", ["float64", "float32", "int16", "bool"])
def test_value_types(dtype):
    """Float64 values are kept; a boolean raster gives 0/1 polygons."""
    array = (
        (ZONES - 1).astype(dtype) if dtype == "bool" else (ZONES * 1.25).astype(dtype)
    )
    cube = raster_to_vector(RasterStack.from_images({D1: _img(array)}))
    expected = {"bool": np.uint8}.get(dtype, np.dtype(dtype))
    assert cube.values.dtype == expected
    assert len(cube) == 2


def test_projected_raster():
    """The cube keeps the raster CRS; its GeoJSON output is in EPSG:4326."""
    utm = CRS.from_epsg(32631)
    cube = raster_to_vector(
        RasterStack.from_images(
            {D1: _img(ZONES, crs=utm, bounds=(500000, 0, 500040, 40))}
        )
    )
    assert cube.crs == utm
    assert shape(cube.geometries[0]).bounds[0] == 500000

    lon, lat = json.loads(encode(cube, "GeoJSON").data)["features"][0]["geometry"][
        "coordinates"
    ][0][0]
    assert lon == pytest.approx(3.0, abs=1e-3)
    assert lat == pytest.approx(0.0, abs=1e-3)


def test_empty_when_all_masked():
    """A fully masked raster gives an empty cube."""
    cube = raster_to_vector(
        RasterStack.from_images({D1: _img(ZONES, mask=np.ones((1, 4, 4), bool))})
    )
    assert len(cube) == 0
    assert (
        encode(cube, "GeoJSON").data == b'{"type": "FeatureCollection", "features": []}'
    )


def test_vectorize_is_an_alias():
    """vectorize gives the same cube as raster_to_vector."""
    stack = RasterStack.from_images({D1: _img(ZONES)})
    a, b = raster_to_vector(stack), vectorize(stack)
    assert a.geometries == b.geometries
    assert a.values.tolist() == b.values.tolist()


def test_zonal_statistics_on_the_polygons():
    """raster_to_vector -> aggregate_spatial: each polygon gets back its value."""
    utm = CRS.from_epsg(32631)
    stack = RasterStack.from_images(
        {D1: _img(ZONES.astype("float32"), crs=utm, bounds=(500000, 0, 500040, 40))}
    )
    zones = process(raster_to_vector)(data=stack)
    stats = process(aggregate_spatial)(
        data=stack, geometries=zones, reducer=lambda data, **kwargs: np.ma.mean(data)
    )

    assert stats.dims == ("geometry", "t")
    assert stats.values[:, 0].tolist() == pytest.approx(zones.values.tolist())


@pytest.mark.parametrize("process_id", ["raster_to_vector", "vectorize"])
def test_process_graph_to_geojson(process_id):
    """A process graph runs the process and saves the polygons as GeoJSON."""
    from openeo_pg_parser_networkx import OpenEOProcessGraph

    from titiler.openeo.processes import process_registry

    graph = {
        "vec": {
            "process_id": process_id,
            "arguments": {"data": {"from_parameter": "data"}},
        },
        "save": {
            "process_id": "save_result",
            "arguments": {"data": {"from_node": "vec"}, "format": "GeoJSON"},
            "result": True,
        },
    }
    run = OpenEOProcessGraph(pg_data={"process_graph": graph}).to_callable(
        process_registry=process_registry
    )
    result = run(named_parameters={"data": RasterStack.from_images({D1: _img(ZONES)})})

    assert result.media_type == "application/geo+json"
    document = json.loads(result.data)
    assert sorted(f["properties"]["value"] for f in document["features"]) == [1, 2]
