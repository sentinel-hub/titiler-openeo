"""Tests for vector_reproject, vector_buffer and filter_vector (ADR 0014 §2.3)."""

import json
import math
from datetime import datetime

import numpy as np
import pytest
from rasterio.crs import CRS
from shapely.geometry import box, mapping, shape

import titiler.openeo.processes as processes
from titiler.openeo.errors import (
    DimensionNotAvailable,
    ProcessParameterInvalid,
    UnitMismatch,
)
from titiler.openeo.processes.implementations.core import process
from titiler.openeo.processes.implementations.data_model import RasterStack
from titiler.openeo.processes.implementations.vector import (
    filter_vector,
    load_geojson,
    vector_buffer,
    vector_reproject,
)
from titiler.openeo.processes.implementations.vector_model import VectorCube

WGS84 = CRS.from_epsg(4326)
UTM = CRS.from_epsg(32631)
D1, D2 = datetime(2023, 1, 5), datetime(2023, 2, 1)


def _points_cube() -> VectorCube:
    """Three points in UTM 31N, 100 m apart, with values for two dates."""
    return VectorCube(
        geometries=[
            {"type": "Point", "coordinates": [500000.0 + 100 * i, 5540000.0]}
            for i in range(3)
        ],
        crs=UTM,
        properties=[{"id": i} for i in range(3)],
        values=np.ma.masked_array(
            np.arange(6, dtype="float64").reshape(3, 2),
            mask=[[False, False], [False, True], [False, False]],
        ),
        dims=("geometry", "t"),
        labels={"t": [D1, D2]},
    )


def _area(bounds, crs=UTM) -> VectorCube:
    return VectorCube(geometries=[mapping(box(*bounds))], crs=crs)


# vector_reproject


@pytest.mark.parametrize(
    "projection",
    [4326, "EPSG:4326", WGS84.to_wkt(version="WKT2_2019")],
    ids=["epsg", "string", "wkt2"],
)
def test_vector_reproject(projection):
    """EPSG code or WKT2: new CRS and coordinates, same values and properties."""
    cube = _points_cube()
    result = vector_reproject(cube, projection)

    assert result.crs == WGS84
    lon, lat = result.geometries[0]["coordinates"]
    assert lon == pytest.approx(3.0, abs=1e-6)
    assert lat == pytest.approx(50.0123, abs=1e-4)
    assert result.properties == cube.properties
    assert result.values.tolist() == cube.values.tolist()
    assert result.dims == cube.dims and result.labels == cube.labels
    # The input cube does not change.
    assert cube.crs == UTM


def test_vector_reproject_round_trip():
    """UTM -> EPSG:4326 -> UTM gives the same coordinates back."""
    cube = _points_cube()
    back = vector_reproject(vector_reproject(cube, 4326), 32631)
    for a, b in zip(cube.geometries, back.geometries):
        assert b["coordinates"] == pytest.approx(a["coordinates"], abs=1e-6)


def test_vector_reproject_keeps_null_geometry():
    """A null geometry stays null."""
    cube = VectorCube(geometries=[None], crs=UTM)
    assert vector_reproject(cube, 4326).geometries == [None]


def test_vector_reproject_dimension():
    """`geometry` is the only geometry dimension of a vector data cube."""
    assert vector_reproject(_points_cube(), 4326, dimension="geometry").crs == WGS84
    with pytest.raises(DimensionNotAvailable) as error:
        vector_reproject(_points_cube(), 4326, dimension="geometries")
    assert error.value.code == "DimensionNotAvailable"


@pytest.mark.parametrize("projection", [1, "not a crs"])
def test_vector_reproject_invalid_projection(projection):
    """An unknown CRS gives ProcessParameterInvalid."""
    with pytest.raises(ProcessParameterInvalid, match="projection"):
        vector_reproject(_points_cube(), projection)


# vector_buffer


def test_vector_buffer_points():
    """Points become circles of the distance in metres; values do not change."""
    cube = _points_cube()
    result = vector_buffer(cube, 10)

    polygons = [shape(g) for g in result.geometries]
    assert {p.geom_type for p in polygons} == {"Polygon"}
    assert polygons[0].area == pytest.approx(math.pi * 100, rel=0.01)
    assert polygons[0].centroid.x == pytest.approx(500000.0)
    assert result.crs == UTM
    assert result.properties == cube.properties
    assert result.values.tolist() == cube.values.tolist()


def test_vector_buffer_negative_can_make_empty():
    """A negative distance shrinks a polygon; it can become empty."""
    cube = _area((0, 0, 100, 100))
    assert shape(vector_buffer(cube, -10).geometries[0]).area == pytest.approx(6400)
    assert shape(vector_buffer(cube, -60).geometries[0]).is_empty


def test_vector_buffer_keeps_null_geometry():
    """A null geometry stays null."""
    cube = VectorCube(geometries=[None], crs=UTM)
    assert vector_buffer(cube, 5).geometries == [None]


def test_vector_buffer_geographic_crs_names_the_utm_zone():
    """EPSG:4326 gives UnitMismatch, with the UTM zone to reproject to."""
    north = load_geojson({"type": "Point", "coordinates": [3.0, 50.0]})
    with pytest.raises(UnitMismatch, match="EPSG:32631") as error:
        vector_buffer(north, 10)
    assert error.value.code == "UnitMismatch"
    assert error.value.status_code == 400

    south = load_geojson({"type": "Point", "coordinates": [-70.0, -33.0]})
    with pytest.raises(UnitMismatch, match="EPSG:32719"):
        vector_buffer(south, 10)


def test_vector_buffer_feet_crs():
    """A projected CRS in feet also gives UnitMismatch."""
    cube = _area((0, 0, 100, 100), crs=CRS.from_epsg(2263))
    with pytest.raises(UnitMismatch, match="US survey foot"):
        vector_buffer(cube, 10)


def test_vector_buffer_zero_distance():
    """The distance must not be 0 (spec)."""
    with pytest.raises(ProcessParameterInvalid, match="not be 0"):
        vector_buffer(_points_cube(), 0)


# filter_vector


@pytest.mark.parametrize(
    "relation,kept",
    [
        # Base: a box from x=499950 to x=500150, around points 0 and 1.
        ("intersects", [0, 1]),
        ("within", [0, 1]),
        ("disjoint", [2]),
        ("contains", []),
        ("touches", []),
    ],
)
def test_filter_vector_relations(relation, kept):
    """A candidate is kept if `relation(candidate, base)` is true."""
    area = _area((499950, 5539950, 500150, 5540050))
    result = filter_vector(_points_cube(), area, relation)
    assert [p["id"] for p in result.properties] == kept


def test_filter_vector_contains():
    """`contains`: the candidate polygons that contain the base geometry."""
    zones = VectorCube(
        geometries=[mapping(box(0, 0, 10, 10)), mapping(box(20, 0, 30, 10))],
        crs=UTM,
        properties=[{"zone": "a"}, {"zone": "b"}],
    )
    point = VectorCube(geometries=[{"type": "Point", "coordinates": [5, 5]}], crs=UTM)
    assert filter_vector(zones, point, "contains").properties == [{"zone": "a"}]


def test_filter_vector_keeps_the_values_of_the_kept_rows():
    """The values and the mask follow the kept geometries."""
    area = _area((500050, 5539950, 500250, 5540050))
    result = filter_vector(_points_cube(), area)

    assert [p["id"] for p in result.properties] == [1, 2]
    assert result.values.tolist() == [[2.0, None], [4.0, 5.0]]
    assert result.dims == ("geometry", "t")
    assert result.labels == {"t": [D1, D2]}


def test_filter_vector_union_and_crs():
    """The union of the base geometries is used, in the CRS of the data."""
    first = box(499950, 5539950, 500050, 5540050)
    last = box(500150, 5539950, 500250, 5540050)
    base = vector_reproject(
        VectorCube(geometries=[mapping(first), mapping(last)], crs=UTM), 4326
    )
    result = filter_vector(_points_cube(), base, "within")
    assert [p["id"] for p in result.properties] == [0, 2]


def test_filter_vector_removes_empty_candidates():
    """Null and empty candidate geometries are removed, also for disjoint."""
    cube = VectorCube(
        geometries=[
            None,
            {"type": "Polygon", "coordinates": []},
            {"type": "Point", "coordinates": [0, 0]},
        ],
        crs=UTM,
    )
    result = filter_vector(cube, _area((10, 10, 20, 20)), "disjoint")
    assert result.geometries == [{"type": "Point", "coordinates": [0, 0]}]


def test_filter_vector_unknown_relation():
    """A relation outside the spec list gives ProcessParameterInvalid."""
    with pytest.raises(ProcessParameterInvalid, match="covers"):
        filter_vector(_points_cube(), _area((0, 0, 1, 1)), "covers")


# Registry and process graphs


def test_raster_cube_is_refused():
    """A raster data cube is not a vector data cube."""
    from rio_tiler.models import ImageData

    stack = RasterStack.from_images(
        {D1: ImageData(np.ma.ones((1, 2, 2)), crs=WGS84, bounds=(0, 0, 1, 1))}
    )
    with pytest.raises(ProcessParameterInvalid, match="geometries"):
        process(vector_buffer)(geometries=stack, distance=10)


def test_processes_hidden_without_the_extra(monkeypatch):
    """Without shapely, vector_buffer and filter_vector are not listed."""
    assert {"vector_reproject", "vector_buffer", "filter_vector"} <= set(
        processes.process_registry[None]
    )
    monkeypatch.setattr(processes, "_module_available", lambda name: False)
    assert processes.process_available("vector_reproject")
    assert not processes.process_available("vector_buffer")
    assert not processes.process_available("filter_vector")


def test_process_graph_buffer_and_filter():
    """load_geojson -> vector_reproject -> vector_buffer -> filter_vector."""
    from openeo_pg_parser_networkx import OpenEOProcessGraph

    points = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [3.0, 50.0]},
                "properties": {"name": "near"},
            },
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [3.1, 50.0]},
                "properties": {"name": "far"},
            },
        ],
    }
    area = {"type": "Point", "coordinates": [3.0005, 50.0]}
    graph = {
        "points": {"process_id": "load_geojson", "arguments": {"data": points}},
        "area": {"process_id": "load_geojson", "arguments": {"data": area}},
        "utm": {
            "process_id": "vector_reproject",
            "arguments": {"data": {"from_node": "points"}, "projection": 32631},
        },
        "buffer": {
            "process_id": "vector_buffer",
            "arguments": {"geometries": {"from_node": "utm"}, "distance": 100},
        },
        "filter": {
            "process_id": "filter_vector",
            "arguments": {
                "data": {"from_node": "buffer"},
                "geometries": {"from_node": "area"},
                "relation": "contains",
            },
        },
        "save": {
            "process_id": "save_result",
            "arguments": {"data": {"from_node": "filter"}, "format": "GeoJSON"},
            "result": True,
        },
    }
    run = OpenEOProcessGraph(pg_data={"process_graph": graph}).to_callable(
        process_registry=processes.process_registry
    )
    document = json.loads(run().data)
    assert [f["properties"]["name"] for f in document["features"]] == ["near"]
    assert document["features"][0]["geometry"]["type"] == "Polygon"
