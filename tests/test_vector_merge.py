"""merge_cubes with two vector data cubes (ADR 0014 §7.3)."""

import json
from datetime import datetime

import numpy as np
import pytest
from openeo_pg_parser_networkx import OpenEOProcessGraph
from rasterio.crs import CRS

from titiler.openeo.errors import (
    IncompatibleGeometryTypes,
    OverlapResolverMissing,
    ProcessParameterInvalid,
    VectorFeatureLimitExceeded,
)
from titiler.openeo.processes import process_registry
from titiler.openeo.processes.implementations.vector import vector_reproject
from titiler.openeo.processes.implementations.vector_model import VectorCube

pytest.importorskip("shapely")

D1, D2, D3 = datetime(2023, 1, 5), datetime(2023, 2, 1), datetime(2023, 3, 1)
UTM = CRS.from_epsg(32631)
merge = process_registry[None]["merge_cubes"].implementation

SQUARE = {
    "type": "Polygon",
    "coordinates": [[[3.0, 50.0], [3.1, 50.0], [3.1, 50.1], [3.0, 50.1], [3.0, 50.0]]],
}
# The same square: its ring starts at another vertex (OGC equality).
SQUARE_ROTATED = {
    "type": "Polygon",
    "coordinates": [[[3.1, 50.1], [3.0, 50.1], [3.0, 50.0], [3.1, 50.0], [3.1, 50.1]]],
}
OTHER = {
    "type": "Polygon",
    "coordinates": [[[4.0, 51.0], [4.1, 51.0], [4.1, 51.1], [4.0, 51.0]]],
}


def _cube(
    geometries, values, dims=("geometry", "t", "bands"), labels=None, properties=None
):
    return VectorCube(
        geometries=geometries,
        properties=properties or [{} for _ in geometries],
        values=np.ma.masked_invalid(np.asarray(values, dtype="float64")),
        dims=dims,
        labels=labels or {"t": [D1], "bands": ["B04"]},
    )


def _add(x, y, context=None):
    return x + y


def test_disjoint_bands_need_no_resolver():
    """Spec example 1: the band labels of cube2 are appended."""
    cube1 = _cube([SQUARE], [[[1.0, 2.0]]], labels={"t": [D1], "bands": ["B1", "B2"]})
    cube2 = _cube([SQUARE], [[[3.0, 4.0]]], labels={"t": [D1], "bands": ["B3", "B4"]})
    result = merge(cube1=cube1, cube2=cube2)
    assert len(result) == 1
    assert result.labels == {"t": [D1], "bands": ["B1", "B2", "B3", "B4"]}
    assert result.values.tolist() == [[[1.0, 2.0, 3.0, 4.0]]]


def test_overlapping_band_needs_a_resolver():
    """Spec example 2: B2 is in both cubes."""
    cube1 = _cube([SQUARE], [[[1.0, 2.0]]], labels={"t": [D1], "bands": ["B1", "B2"]})
    cube2 = _cube([SQUARE], [[[20.0, 3.0]]], labels={"t": [D1], "bands": ["B2", "B3"]})
    with pytest.raises(OverlapResolverMissing) as error:
        merge(cube1=cube1, cube2=cube2)
    assert error.value.code == "OverlapResolverMissing"

    result = merge(cube1=cube1, cube2=cube2, overlap_resolver=_add)
    assert result.labels["bands"] == ["B1", "B2", "B3"]
    assert result.values.tolist() == [[[1.0, 22.0, 3.0]]]


def test_resolver_is_called_once_and_keeps_the_mask():
    """One call with all the overlapping cells; a masked value stays masked."""
    calls = []

    def resolver(x, y, context=None):
        calls.append(x.shape)
        return np.ma.maximum(x, y)

    cube1 = _cube([SQUARE, OTHER], [[[1.0]], [[np.nan]]])
    cube2 = _cube([SQUARE, OTHER], [[[5.0]], [[6.0]]])
    result = merge(cube1=cube1, cube2=cube2, overlap_resolver=resolver)
    assert calls == [(2,)]
    assert result.values.tolist() == [[[5.0]], [[None]]]


def test_new_geometries_are_appended():
    """A geometry that is not in cube1 is appended, with its properties."""
    cube1 = _cube([SQUARE], [[[1.0]]], properties=[{"name": "square", "id": 1}])
    cube2 = _cube(
        [SQUARE_ROTATED, OTHER],
        [[[2.0]], [[3.0]]],
        labels={"t": [D1], "bands": ["B08"]},
        properties=[{"name": "ignored", "area": 9}, {"name": "other"}],
    )
    result = merge(cube1=cube1, cube2=cube2)

    assert result.geometries == [SQUARE, OTHER]
    # cube1 wins for a key in both; cube2 adds its other keys.
    assert result.properties == [
        {"name": "square", "id": 1, "area": 9},
        {"name": "other"},
    ]
    assert result.labels["bands"] == ["B04", "B08"]
    assert result.values.tolist() == [[[1.0, 2.0]], [[None, 3.0]]]


def test_temporal_labels_stay_sorted():
    """Spec: the temporal labels are sorted after the merge."""
    cube1 = _cube([SQUARE], [[[1.0], [3.0]]], labels={"t": [D1, D3], "bands": ["B04"]})
    cube2 = _cube([SQUARE], [[[2.0]]], labels={"t": [D2], "bands": ["B04"]})
    result = merge(cube1=cube1, cube2=cube2)
    assert result.labels["t"] == [D1, D2, D3]
    assert result.values[0, :, 0].tolist() == [1.0, 2.0, 3.0]


def test_lower_dimensional_cube_is_repeated():
    """Spec example 4: a cube without `t` is merged with each date."""
    series = _cube([SQUARE], [[[1.0], [2.0]]], labels={"t": [D1, D2], "bands": ["B04"]})
    elevation = _cube(
        [SQUARE], [[100.0]], dims=("geometry", "bands"), labels={"bands": ["DEM"]}
    )

    result = merge(cube1=series, cube2=elevation)
    assert result.dims == ("geometry", "t", "bands")
    assert result.values.tolist() == [[[1.0, 100.0], [2.0, 100.0]]]

    # The same band name: each date overlaps.
    offset = _cube([SQUARE], [[10.0]], dims=("geometry", "bands"))
    with pytest.raises(OverlapResolverMissing):
        merge(cube1=series, cube2=offset)
    added = merge(cube1=series, cube2=offset, overlap_resolver=_add)
    assert added.values.tolist() == [[[11.0], [12.0]]]


def test_cubes_without_a_common_subset_of_dimensions():
    """Each cube has a dimension that the other does not have."""
    cube1 = _cube([SQUARE], [[1.0]], dims=("geometry", "t"), labels={"t": [D1]})
    cube2 = _cube(
        [SQUARE], [[1.0]], dims=("geometry", "bands"), labels={"bands": ["B04"]}
    )
    with pytest.raises(ProcessParameterInvalid, match="not compatible"):
        merge(cube1=cube1, cube2=cube2)


@pytest.mark.parametrize(
    "geometry,compatible",
    [
        ({"type": "MultiPolygon", "coordinates": [OTHER["coordinates"]]}, True),
        ({"type": "Point", "coordinates": [3.0, 50.0]}, False),
        ({"type": "LineString", "coordinates": [[3.0, 50.0], [4.0, 51.0]]}, False),
    ],
)
def test_geometry_types(geometry, compatible):
    """Spec: a base type merges with its multi type only."""
    cube1 = _cube([SQUARE], [[[1.0]]])
    cube2 = _cube([geometry], [[[2.0]]], labels={"t": [D1], "bands": ["B08"]})
    if compatible:
        assert len(merge(cube1=cube1, cube2=cube2)) == 2
    else:
        with pytest.raises(IncompatibleGeometryTypes) as error:
            merge(cube1=cube1, cube2=cube2)
        assert error.value.code == "IncompatibleGeometryTypes"


def test_duplicate_geometries_match_one_to_one():
    """A cube merged with itself: each geometry matches itself."""
    cube = _cube([SQUARE, SQUARE], [[[1.0]], [[2.0]]])
    result = merge(cube1=cube, cube2=cube, overlap_resolver=_add)
    assert len(result) == 2
    assert result.values.tolist() == [[[2.0]], [[4.0]]]


def test_cube2_is_reprojected_to_the_crs_of_cube1():
    """The result has the CRS of cube1; cube2 geometries are reprojected."""
    point = {"type": "Point", "coordinates": [500000.0, 5540000.0]}
    cube1 = _cube([point], [[[1.0]]])
    cube1.crs = UTM
    other = _cube(
        [{"type": "Point", "coordinates": [4.0, 51.0]}],
        [[[2.0]]],
        labels={"t": [D1], "bands": ["B08"]},
    )
    result = merge(cube1=cube1, cube2=other)
    assert result.crs == UTM
    x, _ = result.geometries[1]["coordinates"]
    assert x == pytest.approx(570168.9, abs=1)


def test_geometries_only():
    """Two cubes without values: the union of their geometries."""
    cube1 = VectorCube(geometries=[SQUARE])
    cube2 = VectorCube(geometries=[SQUARE_ROTATED, OTHER])
    result = merge(cube1=cube1, cube2=cube2)
    assert result.geometries == [SQUARE, OTHER]
    assert result.values is None


def test_feature_limit(monkeypatch):
    """The merged cube counts against max_vector_features."""
    monkeypatch.setenv("TITILER_OPENEO_PROCESSING_MAX_VECTOR_FEATURES", "1")
    with pytest.raises(VectorFeatureLimitExceeded):
        merge(
            cube1=VectorCube(geometries=[SQUARE]), cube2=VectorCube(geometries=[OTHER])
        )


def test_reprojection_round_trip_is_not_equal():
    """OGC equality is exact: a reprojected copy does not match (known limit)."""
    cube = VectorCube(geometries=[SQUARE])
    copy = vector_reproject(vector_reproject(cube, 32631), 4326)
    assert len(merge(cube1=cube, cube2=copy)) == 2


def test_process_graph_with_resolver():
    """load_geojson x 2 -> merge_cubes(max(array_create([x, y]))) -> GeoJSON."""

    def collection(height):
        return {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": SQUARE,
                    "properties": {"name": "square", "height": height},
                }
            ],
        }

    graph = {
        "a": {
            "process_id": "load_geojson",
            "arguments": {"data": collection(10.0), "properties": ["height"]},
        },
        "b": {
            "process_id": "load_geojson",
            "arguments": {"data": collection(25.0), "properties": ["height"]},
        },
        "merged": {
            "process_id": "merge_cubes",
            "arguments": {
                "cube1": {"from_node": "a"},
                "cube2": {"from_node": "b"},
                "overlap_resolver": {
                    "process_graph": {
                        "both": {
                            "process_id": "array_create",
                            "arguments": {
                                "data": [
                                    {"from_parameter": "x"},
                                    {"from_parameter": "y"},
                                ]
                            },
                        },
                        "max": {
                            "process_id": "max",
                            "arguments": {"data": {"from_node": "both"}},
                            "result": True,
                        },
                    }
                },
            },
        },
        "save": {
            "process_id": "save_result",
            "arguments": {"data": {"from_node": "merged"}, "format": "GeoJSON"},
            "result": True,
        },
    }
    run = OpenEOProcessGraph(pg_data={"process_graph": graph}).to_callable(
        process_registry=process_registry
    )
    features = json.loads(run().data)["features"]
    assert len(features) == 1
    assert features[0]["properties"]["values"] == {"height": 25.0}
