"""Generic processes on vector data cubes (ADR 0014 §7.2)."""

import json
from datetime import datetime

import numpy as np
import pytest
from openeo_pg_parser_networkx import OpenEOProcessGraph
from rasterio.crs import CRS
from rio_tiler.models import ImageData

from titiler.openeo.errors import (
    DimensionNotAvailable,
    OpenEOException,
    ProcessParameterInvalid,
)
from titiler.openeo.processes import process_registry
from titiler.openeo.processes.implementations.data_model import RasterStack
from titiler.openeo.processes.implementations.vector import load_geojson
from titiler.openeo.processes.implementations.vector_model import VectorCube

UTM = CRS.from_epsg(32631)
D1, D2 = datetime(2023, 1, 5), datetime(2023, 2, 1)
PROCESSES = process_registry[None]


def _stats_cube() -> VectorCube:
    """A cube like the result of aggregate_spatial: object values, t and bands.

    values[geometry][t][band], one missing value (geometry 1, D2, B08).
    """
    values = np.ma.masked_all((2, 2, 2), dtype=object)
    values[0, 0] = [1, 10]
    values[0, 1] = [3, 30]
    values[1, 0] = [2, 20]
    values[1, 1, 0] = 4
    return VectorCube(
        geometries=[
            {"type": "Point", "coordinates": [3.0, 50.0]},
            {"type": "Point", "coordinates": [4.0, 51.0]},
        ],
        properties=[{"name": "a"}, {"name": "b"}],
        values=values,
        dims=("geometry", "t", "bands"),
        labels={"t": [D1, D2], "bands": ["B04", "B08"]},
    )


def _node(process_id: str, **arguments) -> dict:
    return {"process_id": process_id, "arguments": arguments}


def _callback(process_id: str, **arguments) -> dict:
    """A callback process graph with one node."""
    return {"process_graph": {"cb": {**_node(process_id, **arguments), "result": True}}}


def _run(process_id: str, cube=None, **arguments):
    """Run one process in a process graph, with the cube as parameter `data`."""
    graph = {
        "node": {
            **_node(process_id, data={"from_parameter": "data"}, **arguments),
            "result": True,
        }
    }
    run = OpenEOProcessGraph(pg_data={"process_graph": graph}).to_callable(
        process_registry=process_registry
    )
    return run(named_parameters={"data": _stats_cube() if cube is None else cube})


# reduce_dimension


@pytest.mark.parametrize(
    "dimension,reducer,dims,expected",
    [
        ("t", "mean", ("geometry", "bands"), [[2.0, 20.0], [3.0, 20.0]]),
        ("temporal", "last", ("geometry", "bands"), [[3, 30], [4, None]]),
        ("bands", "sum", ("geometry", "t"), [[11, 33], [22, 4]]),
        ("spectral", "max", ("geometry", "t"), [[10, 30], [20, 4]]),
    ],
)
def test_reduce_dimension(dimension, reducer, dims, expected):
    """The reducer gets the values with the dimension on axis 0."""
    result = _run(
        "reduce_dimension",
        dimension=dimension,
        reducer=_callback(reducer, data={"from_parameter": "data"}),
    )
    assert result.dims == dims
    assert result.values.tolist() == expected
    other = dims[1]
    assert result.labels == {other: _stats_cube().labels[other]}
    assert result.properties == [{"name": "a"}, {"name": "b"}]


def test_reduce_dimension_array_element():
    """array_element(index) selects one band; a missing value stays missing."""
    result = _run(
        "reduce_dimension",
        dimension="bands",
        reducer=_callback("array_element", data={"from_parameter": "data"}, index=1),
    )
    assert result.values.tolist() == [[10, 30], [20, None]]


def test_reduce_all_dimensions_to_geojson():
    """t then bands: one value per geometry, in the GeoJSON property `value`."""
    cube = _run(
        "reduce_dimension",
        dimension="t",
        reducer=_callback("first", data={"from_parameter": "data"}),
    )
    cube = _run(
        "reduce_dimension",
        cube,
        dimension="bands",
        reducer=_callback("max", data={"from_parameter": "data"}),
    )
    assert cube.dims == ("geometry",)
    features = cube.to_feature_collection()["features"]
    assert [f["properties"]["value"] for f in features] == [10, 20]


def test_reduce_dimension_errors():
    """geometry, an unknown dimension, a bad reducer and no values."""
    mean = _callback("mean", data={"from_parameter": "data"})
    with pytest.raises(ProcessParameterInvalid, match="'geometry' dimension"):
        _run("reduce_dimension", dimension="geometry", reducer=mean)
    with pytest.raises(DimensionNotAvailable, match="'x'"):
        _run("reduce_dimension", dimension="x", reducer=mean)

    reduce_dimension = PROCESSES["reduce_dimension"].implementation
    with pytest.raises(
        ProcessParameterInvalid, match=r"\(shape \(2, 2\)\), got the shape \(\)"
    ):
        reduce_dimension(
            data=_stats_cube(), dimension="t", reducer=lambda data: np.ma.sum(data)
        )
    # A cube without values has the geometry dimension only.
    geometries = VectorCube(geometries=[{"type": "Point", "coordinates": [0, 0]}])
    with pytest.raises(DimensionNotAvailable, match="'t'"):
        reduce_dimension(data=geometries, dimension="t", reducer=lambda data: data)


# apply


def test_apply_keeps_the_mask_and_the_labels():
    """apply(multiply(x, 2)): each value; the missing value stays missing."""
    result = _run(
        "apply", process=_callback("multiply", x={"from_parameter": "x"}, y=2)
    )
    assert result.values.tolist() == [[[2, 20], [6, 60]], [[4, 40], [8, None]]]
    assert result.dims == ("geometry", "t", "bands")
    assert result.labels == _stats_cube().labels


def test_apply_with_context():
    """The context reaches the process."""
    result = _run(
        "apply",
        process=_callback(
            "gt", x={"from_parameter": "x"}, y={"from_parameter": "context"}
        ),
        context=5,
    )
    assert result.values[:, 0, 1].tolist() == [True, True]
    assert result.values[:, 0, 0].tolist() == [False, False]


def test_apply_without_values():
    """A cube of geometries only has no values to apply the process to."""
    geometries = VectorCube(geometries=[{"type": "Point", "coordinates": [0, 0]}])
    with pytest.raises(ProcessParameterInvalid, match="no values, only geometries"):
        PROCESSES["apply"].implementation(
            data=geometries, process=lambda x, **kwargs: x
        )


# filter_temporal, filter_bands, filter_labels


def test_filter_temporal():
    """The `t` labels in [start, end) are kept, with their values."""
    result = _run("filter_temporal", extent=["2023-01-10", None])
    assert result.labels["t"] == [D2]
    assert result.values.tolist() == [[[3, 30]], [[4, None]]]


def test_filter_temporal_without_t():
    """A cube without a `t` dimension."""
    cube = load_geojson({"type": "Point", "coordinates": [0, 0]})
    with pytest.raises(DimensionNotAvailable, match="'t'"):
        PROCESSES["filter_temporal"].implementation(
            data=cube, extent=["2023-01-01", "2024-01-01"]
        )


def test_filter_bands():
    """The bands in the given order."""
    result = _run("filter_bands", bands=["B08", "B04"])
    assert result.labels["bands"] == ["B08", "B04"]
    assert result.values[0].tolist() == [[10, 1], [30, 3]]


def test_filter_bands_errors():
    """A band that does not exist, and a cube without bands."""
    with pytest.raises(OpenEOException, match=r"\['B02'\] do not exist"):
        _run("filter_bands", bands=["B02"])
    cube = load_geojson(
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [0, 0]},
            "properties": {"B04": 1},
        },
        properties=["B04"],
    )
    with pytest.raises(DimensionNotAvailable, match="'bands'"):
        PROCESSES["filter_bands"].implementation(data=cube, bands=["B04"])


@pytest.mark.parametrize(
    "dimension,condition,labels",
    [
        ("bands", _callback("eq", x={"from_parameter": "value"}, y="B08"), ["B08"]),
        (
            "t",
            _callback("gte", x={"from_parameter": "value"}, y="2023-01-10T00:00:00Z"),
            [D2],
        ),
    ],
)
def test_filter_labels(dimension, condition, labels):
    """The condition gets the labels; dates are UTC datetimes."""
    result = _run("filter_labels", dimension=dimension, condition=condition)
    assert result.labels[dimension] == labels
    assert result.values.shape[1:] == (1, 2) if dimension == "t" else (2, 1)


def test_filter_labels_load_geojson_properties():
    """The `properties` dimension of load_geojson has labels too."""
    cube = load_geojson(
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [0, 0]},
            "properties": {"height": 12.0, "width": 3.0},
        },
        properties=["height", "width"],
    )
    result = _run(
        "filter_labels",
        cube,
        dimension="properties",
        condition=_callback("eq", x={"from_parameter": "value"}, y="width"),
    )
    assert result.labels == {"properties": ["width"]}
    assert result.values.tolist() == [[3.0]]


def test_filter_labels_errors():
    """No label matches; the geometry dimension."""
    never = _callback("eq", x={"from_parameter": "value"}, y="none")
    with pytest.raises(
        OpenEOException, match="No label of the dimension 'bands'"
    ) as error:
        _run("filter_labels", dimension="bands", condition=never)
    assert error.value.code == "NoDataAvailable"
    with pytest.raises(ProcessParameterInvalid, match="filter_vector or filter_bbox"):
        _run("filter_labels", dimension="geometry", condition=never)


# rename_labels


def test_rename_labels():
    """By position, with source, and for `t`. The values do not change."""
    rename = PROCESSES["rename_labels"].implementation
    cube = _stats_cube()
    assert rename(data=cube, dimension="bands", target=["red", "nir"]).labels[
        "bands"
    ] == ["red", "nir"]
    renamed = rename(data=cube, dimension="bands", target=["nir"], source=["B08"])
    assert renamed.labels["bands"] == ["B04", "nir"]
    assert renamed.values.tolist() == cube.values.tolist()
    dates = rename(
        data=cube, dimension="t", target=["2024-01-01"], source=["2023-01-05"]
    )
    assert dates.labels["t"] == [datetime(2024, 1, 1), D2]


def test_rename_labels_errors():
    """A label that exists already, and an unknown dimension."""
    rename = PROCESSES["rename_labels"].implementation
    with pytest.raises(OpenEOException, match="already exists"):
        rename(data=_stats_cube(), dimension="bands", target=["B04"], source=["B08"])
    with pytest.raises(DimensionNotAvailable):
        rename(data=_stats_cube(), dimension="properties", target=["a", "b"])


# filter_bbox


def _bbox_cube() -> VectorCube:
    return VectorCube(
        geometries=[
            {"type": "Point", "coordinates": [3.0, 50.0]},
            # Partly outside the box: removed.
            {"type": "LineString", "coordinates": [[3.5, 50.5], [5.0, 50.5]]},
            {
                "type": "Polygon",
                "coordinates": [[[3.1, 50.1], [3.4, 50.1], [3.4, 50.4], [3.1, 50.1]]],
            },
            None,
            {"type": "Polygon", "coordinates": []},
        ],
        properties=[{"id": i} for i in range(5)],
    )


def test_filter_bbox_keeps_geometries_fully_within():
    """openEO: a geometry is kept if it is fully within the box."""
    extent = {"west": 2.5, "south": 49.5, "east": 4.0, "north": 51.0}
    result = _run("filter_bbox", _bbox_cube(), extent=extent)
    assert [p["id"] for p in result.properties] == [0, 2]


def test_filter_bbox_in_another_crs():
    """A box in UTM: the geometries are reprojected to the CRS of the box."""
    # The point (3.0, 50.0) is at about (500000, 5538631) in UTM 31N.
    extent = {
        "west": 499000,
        "south": 5537000,
        "east": 501000,
        "north": 5540000,
        "crs": 32631,
    }
    result = _run("filter_bbox", _bbox_cube(), extent=extent)
    assert [p["id"] for p in result.properties] == [0]


def test_filter_bbox_keeps_values():
    """The values follow the kept geometries."""
    extent = {"west": 3.5, "south": 50.5, "east": 4.5, "north": 51.5}
    result = _run("filter_bbox", extent=extent)
    assert [p["name"] for p in result.properties] == ["b"]
    assert result.values.tolist() == [[[2, 20], [4, None]]]


# A full graph


def test_aggregate_spatial_graph_to_csv():
    """aggregate_spatial -> filter_temporal -> reduce_dimension -> rename_labels."""
    image = ImageData(
        np.ma.masked_array(
            np.stack([np.full((4, 4), 1.0), np.full((4, 4), 10.0)]),
        ),
        crs=UTM,
        bounds=(500000.0, 5540000.0, 500040.0, 5540040.0),
        band_descriptions=["B04", "B08"],
    )
    stack = RasterStack.from_images(
        {
            D1: image,
            D2: ImageData(
                image.array * 3,
                crs=UTM,
                bounds=image.bounds,
                band_descriptions=["B04", "B08"],
            ),
        }
    )
    square = {
        "type": "Polygon",
        "coordinates": [
            [
                [500000.0, 5540000.0],
                [500040.0, 5540000.0],
                [500040.0, 5540040.0],
                [500000.0, 5540000.0],
            ]
        ],
    }
    from rasterio.warp import transform_geom

    zones = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": transform_geom(UTM, "EPSG:4326", square),
                "properties": {"name": "zone"},
            }
        ],
    }
    graph = {
        "stats": _node(
            "aggregate_spatial",
            data={"from_parameter": "data"},
            geometries=zones,
            reducer=_callback("mean", data={"from_parameter": "data"}),
        ),
        "late": _node(
            "filter_temporal", data={"from_node": "stats"}, extent=["2023-01-10", None]
        ),
        "mean": _node(
            "reduce_dimension",
            data={"from_node": "late"},
            dimension="t",
            reducer=_callback("mean", data={"from_parameter": "data"}),
        ),
        "named": _node(
            "rename_labels",
            data={"from_node": "mean"},
            dimension="bands",
            target=["red", "nir"],
        ),
        "save": {
            **_node("save_result", data={"from_node": "named"}, format="GeoJSON"),
            "result": True,
        },
    }
    run = OpenEOProcessGraph(pg_data={"process_graph": graph}).to_callable(
        process_registry=process_registry
    )
    document = json.loads(run(named_parameters={"data": stack}).data)
    properties = document["features"][0]["properties"]
    assert properties["name"] == "zone"
    assert properties["values"] == {"red": 3.0, "nir": 30.0}
