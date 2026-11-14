"""Clear errors for vector data cubes in raster processes (ADR 0014 §7.1)."""

from datetime import datetime

import numpy as np
import pytest
from rasterio.crs import CRS
from rio_tiler.models import ImageData

from titiler.openeo.errors import FeatureUnsupported, ProcessParameterInvalid
from titiler.openeo.processes import process_registry
from titiler.openeo.processes.implementations.core import VECTOR_CUBE_NOT_YET_SUPPORTED
from titiler.openeo.processes.implementations.data_model import RasterStack
from titiler.openeo.processes.implementations.vector_model import VectorCube

UTM = CRS.from_epsg(32631)
D1 = datetime(2023, 1, 5)
PROCESSES = process_registry[None]


def _run(process_id: str, **arguments):
    return PROCESSES[process_id].implementation(**arguments)


@pytest.fixture
def vector() -> VectorCube:
    return VectorCube(
        geometries=[{"type": "Point", "coordinates": [500005.0, 5540005.0]}],
        crs=UTM,
        properties=[{"name": "a"}],
        values=np.ma.masked_array([1.0]),
    )


@pytest.fixture
def raster() -> RasterStack:
    image = ImageData(
        np.ma.ones((2, 4, 4)),
        crs=UTM,
        bounds=(500000.0, 5540000.0, 500040.0, 5540040.0),
        band_descriptions=["B04", "B08"],
    )
    return RasterStack.from_images({D1: image})


@pytest.mark.parametrize(
    "process_id,arguments",
    [
        ("ndvi", {}),
        ("resample_spatial", {"resolution": 10}),
        ("mask_polygon", {"mask": {"type": "Point", "coordinates": [0, 0]}}),
        ("raster_to_vector", {}),
        ("aggregate_spatial", {"geometries": None, "reducer": "mean"}),
    ],
)
def test_raster_process_needs_a_raster_cube(vector, process_id, arguments):
    """A raster-only process names both cube kinds and vector_to_raster."""
    if "geometries" in arguments:
        arguments["geometries"] = vector
    with pytest.raises(ProcessParameterInvalid) as error:
        _run(process_id, data=vector, **arguments)
    message = str(error.value)
    assert f"Process '{process_id}' needs a raster data cube" in message
    assert "parameter 'data'" in message
    assert "got a vector data cube (dimension geometry)" in message
    assert message.endswith("Use vector_to_raster to convert it.")
    assert error.value.status_code == 400


def test_hint_does_not_name_the_failing_process(vector):
    """vector_to_raster with a vector target: no "use vector_to_raster" hint."""
    with pytest.raises(ProcessParameterInvalid) as error:
        _run("vector_to_raster", data=vector, target=vector)
    message = str(error.value)
    assert "parameter 'target'" in message
    assert "Use vector_to_raster" not in message


@pytest.mark.parametrize(
    "process_id,parameter,hint",
    [
        (
            "vector_buffer",
            "geometries",
            "Use raster_to_vector, aggregate_spatial or load_geojson",
        ),
        ("aggregate_spatial", "geometries", "Use raster_to_vector or load_geojson"),
        (
            "vector_reproject",
            "data",
            "Use raster_to_vector, aggregate_spatial or load_geojson",
        ),
    ],
)
def test_vector_process_needs_a_vector_cube(raster, process_id, parameter, hint):
    """A raster data cube in a vector parameter names both cube kinds."""
    arguments = {"data": raster, parameter: raster}
    if process_id == "aggregate_spatial":
        arguments["reducer"] = "mean"
    if process_id == "vector_buffer":
        arguments = {"geometries": raster, "distance": 1}
    if process_id == "vector_reproject":
        arguments["projection"] = 4326
    with pytest.raises(ProcessParameterInvalid) as error:
        _run(process_id, **arguments)
    message = str(error.value)
    assert (
        f"needs a vector data cube (dimension geometry) for the parameter '{parameter}'"
        in message
    )
    assert "got a raster data cube (dimensions x and y)" in message
    assert hint in message


@pytest.mark.parametrize(
    "process_id", sorted(VECTOR_CUBE_NOT_YET_SUPPORTED & set(PROCESSES))
)
def test_generic_processes_are_not_supported_yet(vector, process_id):
    """openEO permits a vector cube here: 501 FeatureUnsupported, not 400."""
    with pytest.raises(FeatureUnsupported) as error:
        _run(process_id, data=vector)
    assert error.value.code == "FeatureUnsupported"
    assert error.value.status_code == 501
    assert f"Process '{process_id}' does not support vector data cubes yet" in str(
        error.value
    )


@pytest.mark.parametrize("vector_first", [True, False])
def test_merge_cubes_vector_and_raster(vector, raster, vector_first):
    """The openEO specification does not permit this merge."""
    cubes = (vector, raster) if vector_first else (raster, vector)
    with pytest.raises(
        ProcessParameterInvalid, match="not possible to merge a vector and a raster"
    ):
        _run("merge_cubes", cube1=cubes[0], cube2=cubes[1])


def test_merge_cubes_two_vector_cubes_not_yet(vector):
    """openEO permits this merge; it comes later."""
    with pytest.raises(FeatureUnsupported, match="merge_cubes"):
        _run("merge_cubes", cube1=vector, cube2=vector)


def test_merge_cubes_two_raster_cubes_still_works(raster):
    """The Union hint does not change the raster merge."""
    other = RasterStack.from_images(
        {
            D1: ImageData(
                np.ma.zeros((1, 4, 4)),
                crs=UTM,
                bounds=raster.bounds,
                band_descriptions=["mask"],
            )
        }
    )
    merged = _run("merge_cubes", cube1=raster, cube2=other)
    assert merged[D1].band_descriptions == ["B04", "B08", "mask"]


@pytest.mark.parametrize("format_name", ["GPKG", "FlatGeobuf"])
def test_save_result_keeps_the_cube_crs(vector, format_name, tmp_path):
    """save_result gets the cube itself, not GeoJSON in EPSG:4326."""
    pyogrio = pytest.importorskip("pyogrio")
    result = _run("save_result", data=vector, format=format_name)
    path = tmp_path / result.filename
    path.write_bytes(result.data)
    info = pyogrio.read_info(path)
    assert CRS.from_user_input(info["crs"]) == UTM


def test_save_result_geoparquet_keeps_the_cube_crs(vector):
    """The same for GeoParquet (the pyogrio wheel has no Parquet driver)."""
    pytest.importorskip("arro3.io")
    from tests.test_vector_formats import _read_parquet

    _, geo = _read_parquet(_run("save_result", data=vector, format="GeoParquet").data)
    assert geo["columns"]["geometry"]["crs"]["id"]["code"] == 32631


def test_result_endpoint_gives_the_error(app_with_auth):
    """POST /result: 400 with the code and the message of the error."""
    graph = {
        "process": {
            "process_graph": {
                "vector": {
                    "process_id": "load_geojson",
                    "arguments": {
                        "data": {"type": "Point", "coordinates": [3.0, 50.0]}
                    },
                },
                "ndvi": {
                    "process_id": "ndvi",
                    "arguments": {"data": {"from_node": "vector"}},
                },
                "save": {
                    "process_id": "save_result",
                    "arguments": {"data": {"from_node": "ndvi"}, "format": "GTiff"},
                    "result": True,
                },
            }
        }
    }
    response = app_with_auth.post("/result", json=graph)
    assert response.status_code == 400
    body = response.json()
    assert body["code"] == "ProcessParameterInvalid"
    assert "needs a raster data cube" in body["message"]
