"""Tests for vector_to_raster (ADR 0014 §2.3)."""

import json
import warnings
from datetime import datetime
from pathlib import Path

import numpy as np
import pytest
from rasterio.crs import CRS
from rio_tiler.models import ImageData

from titiler.openeo.errors import OutputLimitExceeded, ProcessParameterInvalid
from titiler.openeo.processes.implementations.core import process
from titiler.openeo.processes.implementations.data_model import RasterStack
from titiler.openeo.processes.implementations.reduce import REDUCED_TEMPORAL_SENTINEL
from titiler.openeo.processes.implementations.vector import (
    load_geojson,
    raster_to_vector,
    vector_reproject,
    vector_to_raster,
)
from titiler.openeo.processes.implementations.vector_model import VectorCube

UTM = CRS.from_epsg(32631)
D1, D2 = datetime(2023, 1, 5), datetime(2023, 2, 1)

# A 4 x 4 grid of 10 m pixels. Row 0 is the north row (y from 30 to 40).
BOUNDS = (500000.0, 5540000.0, 500040.0, 5540040.0)


def _target(count: int = 1) -> RasterStack:
    image = ImageData(np.ma.zeros((count, 4, 4)), crs=UTM, bounds=BOUNDS)
    return RasterStack.from_images({D1: image})


def _box(col0: int, row0: int, col1: int, row1: int) -> dict:
    """A polygon on whole pixels: columns col0..col1-1, rows row0..row1-1."""
    x0, x1 = BOUNDS[0] + 10 * col0, BOUNDS[0] + 10 * col1
    y0, y1 = BOUNDS[3] - 10 * row1, BOUNDS[3] - 10 * row0
    return {
        "type": "Polygon",
        "coordinates": [[[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]],
    }


def _cube(values, dims=("geometry",), labels=None, geometries=None) -> VectorCube:
    geometries = geometries or [_box(0, 0, 3, 3), _box(1, 1, 4, 4)]
    return VectorCube(
        geometries=geometries,
        crs=UTM,
        values=np.ma.masked_invalid(np.asarray(values, dtype="float64")),
        dims=dims,
        labels=labels or {},
    )


def test_last_geometry_wins_and_uncovered_pixels_are_masked():
    """Overlap: the last geometry. No geometry: no data. Grid of the target."""
    result = vector_to_raster(_cube([1, 2]), _target(count=3))

    assert list(result.keys()) == [REDUCED_TEMPORAL_SENTINEL]
    image = result[REDUCED_TEMPORAL_SENTINEL]
    assert image.crs == UTM
    assert tuple(image.bounds) == BOUNDS
    assert (image.width, image.height, image.count) == (4, 4, 1)
    assert image.band_descriptions == ["value"]
    assert image.array[0].filled(0).tolist() == [
        [1, 1, 1, 0],
        [1, 2, 2, 2],
        [1, 2, 2, 2],
        [0, 2, 2, 2],
    ]
    assert image.array.mask[0, 0, 3] and image.array.mask[0, 3, 0]
    assert image.array.dtype == np.float64


def test_masked_value_gives_no_data():
    """A pixel of a geometry whose value is no-data has no data."""
    image = vector_to_raster(_cube([1, np.nan]), _target())[REDUCED_TEMPORAL_SENTINEL]
    assert image.array.mask[0].tolist() == [
        [False, False, False, True],
        [False, True, True, True],
        [False, True, True, True],
        [True, True, True, True],
    ]


def test_geometries_are_reprojected_to_the_target_crs():
    """A cube in EPSG:4326 is rasterized on the UTM grid of the target."""
    cube = vector_reproject(_cube([7, 7], geometries=[_box(0, 0, 2, 2)] * 2), 4326)
    image = vector_to_raster(cube, _target())[REDUCED_TEMPORAL_SENTINEL]
    assert image.crs == UTM
    assert image.array[0].filled(0).tolist() == [
        [7, 7, 0, 0],
        [7, 7, 0, 0],
        [0, 0, 0, 0],
        [0, 0, 0, 0],
    ]


@pytest.mark.parametrize(
    "dims",
    [("geometry", "t", "bands"), ("geometry", "bands", "t")],
    ids=["t-bands", "bands-t"],
)
def test_dates_and_bands(dims):
    """`t` labels become the dates, the other dimension becomes the bands."""
    # values[geometry][date][band]
    values = np.array(
        [[[1, 10], [3, 30]], [[2, 20], [4, np.nan]]],
        dtype="float64",
    )
    if dims[1] == "bands":
        values = values.transpose(0, 2, 1)
    cube = _cube(values, dims=dims, labels={"t": [D1, D2], "bands": ["B04", "B08"]})

    result = vector_to_raster(cube, _target())

    assert list(result.keys()) == [D1, D2]
    first, second = result[D1], result[D2]
    assert first.band_descriptions == ["B04", "B08"]
    assert first.array[:, 1, 1].tolist() == [2, 20]
    assert first.array[:, 0, 0].tolist() == [1, 10]
    assert second.array[:, 0, 0].tolist() == [3, 30]
    assert second.array[0, 1, 1] == 4
    assert second.array.mask[1, 1, 1]


def test_aggregate_spatial_result_keeps_the_reduced_date():
    """A `t` label from a reduced cube stays the reduced-time key."""
    cube = _cube(
        [[1], [2]], dims=("geometry", "t"), labels={"t": [REDUCED_TEMPORAL_SENTINEL]}
    )
    assert list(vector_to_raster(cube, _target()).keys()) == [REDUCED_TEMPORAL_SENTINEL]


def test_text_date_labels():
    """ISO 8601 text labels of `t` are dates."""
    cube = _cube(
        [[1], [2]], dims=("geometry", "t"), labels={"t": ["2023-01-05T00:00:00Z"]}
    )
    assert list(vector_to_raster(cube, _target()).keys()) == [D1]

    cube.labels["t"] = ["first"]
    with pytest.raises(ProcessParameterInvalid, match="'first'.*not a date"):
        vector_to_raster(cube, _target())


def test_load_geojson_properties_become_bands():
    """load_geojson(properties=...) -> one band for each property."""
    features = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": geometry,
                "properties": {"class": n, "height": 2.5 * n, "name": f"zone {n}"},
            }
            for n, geometry in enumerate([_box(0, 0, 2, 2), _box(2, 2, 4, 4)], 1)
        ],
    }
    cube = load_geojson(features, properties=["class", "height"])
    cube.crs = UTM  # The test coordinates are UTM metres.

    image = vector_to_raster(cube, _target())[REDUCED_TEMPORAL_SENTINEL]
    assert image.band_descriptions == ["class", "height"]
    assert image.array[:, 0, 0].tolist() == [1.0, 2.5]
    assert image.array[:, 3, 3].tolist() == [2.0, 5.0]

    text = load_geojson(features, properties=["name"])
    text.crs = UTM
    with pytest.raises(ProcessParameterInvalid, match="must be numbers"):
        vector_to_raster(text, _target())


@pytest.mark.parametrize(
    "cells,dtype",
    [([1, 2], np.int64), ([True, False], np.uint8), ([1, 2.5], np.float64)],
    ids=["int", "bool", "mixed"],
)
def test_object_values_get_a_numeric_dtype(cells, dtype):
    """aggregate_spatial gives object values: ints stay ints."""
    values = np.ma.masked_array(np.array(cells, dtype=object), mask=[False, False])
    cube = VectorCube(
        geometries=[_box(0, 0, 1, 1), _box(1, 0, 2, 1)], crs=UTM, values=values
    )
    image = vector_to_raster(cube, _target())[REDUCED_TEMPORAL_SENTINEL]
    assert image.array.dtype == dtype
    assert image.array[0, 0, :2].tolist() == [dtype(c) for c in cells]


def test_raster_to_vector_round_trip():
    """raster_to_vector -> vector_to_raster gives the raster back."""
    classes = np.ma.masked_array(
        np.array([[1, 1, 2, 2], [1, 3, 3, 2], [4, 4, 3, 2], [4, 4, 0, 0]], "uint8"),
        mask=np.array([[0, 0, 0, 0], [0, 0, 0, 0], [0, 0, 0, 0], [0, 0, 1, 1]], bool),
    )
    stack = RasterStack.from_images(
        {D1: ImageData(classes[np.newaxis], crs=UTM, bounds=BOUNDS)}
    )
    image = vector_to_raster(raster_to_vector(stack), stack)[REDUCED_TEMPORAL_SENTINEL]
    assert image.array.dtype == np.uint8
    assert image.array[0].tolist() == classes.tolist()


def test_points_lines_null_and_empty_geometries():
    """Points and lines cover the pixels on their path; null and empty are skipped."""
    y_row1 = BOUNDS[3] - 15
    cube = _cube(
        [5, 6, 7, 8],
        geometries=[
            {"type": "Point", "coordinates": [BOUNDS[0] + 35, BOUNDS[3] - 5]},
            {
                "type": "LineString",
                "coordinates": [[BOUNDS[0], y_row1], [BOUNDS[2], y_row1]],
            },
            None,
            {"type": "Polygon", "coordinates": []},
        ],
    )
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        image = vector_to_raster(cube, _target())[REDUCED_TEMPORAL_SENTINEL]
    assert image.array[0].filled(0).tolist() == [
        [0, 0, 0, 5],
        [6, 6, 6, 6],
        [0, 0, 0, 0],
        [0, 0, 0, 0],
    ]


def test_cube_without_geometries():
    """No geometry: a raster with no data."""
    cube = VectorCube(
        geometries=[], crs=UTM, values=np.ma.masked_array(np.zeros(0, "float32"))
    )
    image = vector_to_raster(cube, _target())[REDUCED_TEMPORAL_SENTINEL]
    assert image.array.mask.all()
    assert image.array.dtype == np.float32


def test_target_pixels_are_not_read():
    """The grid comes from the target metadata: its tasks do not run."""

    def task():
        raise AssertionError("the target pixels were read")

    target = RasterStack(
        tasks=[(task, {"datetime": D1})],
        timestamp_fn=lambda asset: asset["datetime"],
        width=4,
        height=4,
        bounds=BOUNDS,
        dst_crs=UTM,
    )
    image = vector_to_raster(_cube([1, 2]), target)[REDUCED_TEMPORAL_SENTINEL]
    assert image.array[0, 3, 3] == 2


def test_cube_without_values():
    """A cube of geometries only has nothing to rasterize."""
    cube = VectorCube(geometries=[_box(0, 0, 1, 1)], crs=UTM)
    with pytest.raises(ProcessParameterInvalid, match="no values.*properties"):
        vector_to_raster(cube, _target())


def test_two_other_dimensions():
    """aggregate_spatial with target_dimension: reduce or filter first."""
    cube = _cube(
        np.ones((2, 1, 1, 3)),
        dims=("geometry", "t", "bands", "result"),
        labels={
            "t": [D1],
            "bands": ["B04"],
            "result": ["value", "total_count", "valid_count"],
        },
    )
    with pytest.raises(ProcessParameterInvalid, match=r"\['result'\]"):
        vector_to_raster(cube, _target())


def test_output_limit(monkeypatch):
    """The result counts against max_pixels: width x height x dates x bands."""
    monkeypatch.setenv("TITILER_OPENEO_PROCESSING_MAX_PIXELS", "31")
    cube = _cube(
        np.ones((2, 2)), dims=("geometry", "bands"), labels={"bands": ["a", "b"]}
    )
    with pytest.raises(OutputLimitExceeded, match="4x4 pixels x 1 items x 2 bands"):
        vector_to_raster(cube, _target())

    monkeypatch.setenv("TITILER_OPENEO_PROCESSING_MAX_PIXELS", "32")
    assert vector_to_raster(cube, _target())


def test_decorator_checks_the_cube_kinds():
    """`data` must be a vector data cube and `target` a raster data cube."""
    wrapped = process(vector_to_raster)
    assert wrapped(data=_cube([1, 2]), target=_target())
    with pytest.raises(ProcessParameterInvalid, match="data"):
        wrapped(data=_target(), target=_target())
    with pytest.raises(ProcessParameterInvalid, match="target"):
        wrapped(data=_cube([1, 2]), target=_cube([1, 2]))


def test_spec_target_is_the_second_parameter():
    """The openEO Python client reads the name of the second parameter."""
    spec_file = (
        Path(__file__).parents[1]
        / "titiler/openeo/processes/data/vector_to_raster.json"
    )
    spec = json.loads(spec_file.read_text())
    assert [p["name"] for p in spec["parameters"]] == ["data", "target"]


def test_process_graph_load_geojson_to_gtiff():
    """load_geojson (EPSG:4326) -> vector_to_raster -> save_result GTiff."""
    import rasterio
    from openeo_pg_parser_networkx import OpenEOProcessGraph
    from rasterio.warp import transform_geom

    from titiler.openeo.processes import process_registry

    features = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": transform_geom(UTM, "EPSG:4326", _box(0, 0, 2, 4)),
                "properties": {"class": 3},
            }
        ],
    }
    graph = {
        "vector": {
            "process_id": "load_geojson",
            "arguments": {"data": features, "properties": ["class"]},
        },
        "raster": {
            "process_id": "vector_to_raster",
            "arguments": {
                "data": {"from_node": "vector"},
                "target": {"from_parameter": "target"},
            },
        },
        "save": {
            "process_id": "save_result",
            "arguments": {"data": {"from_node": "raster"}, "format": "GTiff"},
            "result": True,
        },
    }
    run = OpenEOProcessGraph(pg_data={"process_graph": graph}).to_callable(
        process_registry=process_registry
    )
    result = run(named_parameters={"target": _target()})

    with rasterio.MemoryFile(result.data) as memfile, memfile.open() as dataset:
        assert dataset.crs == UTM
        assert tuple(dataset.bounds) == BOUNDS
        assert dataset.read(1, masked=True).tolist() == [[3, 3, None, None]] * 4
