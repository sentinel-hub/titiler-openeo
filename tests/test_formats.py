"""Test titiler.openeo.processes.implementations.formats."""

import json
from datetime import datetime, timezone

import numpy
import pytest
from openeo_pg_parser_networkx.process_registry import Process
from rio_tiler.models import ImageData

from titiler.openeo.errors import FormatUnsuitable
from titiler.openeo.models.openapi import FileFormats
from titiler.openeo.processes.implementations.core import process
from titiler.openeo.processes.implementations.data_model import RasterStack
from titiler.openeo.processes.implementations.formats import (
    OUTPUT_FORMATS,
    SaveResultData,
    _to_json_safe,
    data_kind,
    encode,
    encode_result,
    file_formats_document,
    get_output_format,
)

T1 = datetime(2023, 1, 5, 10, 56, 21, tzinfo=timezone.utc)
T2 = datetime(2023, 1, 10, 10, 56, 21, tzinfo=timezone.utc)


@pytest.fixture
def vector_cube():
    """Return a vector cube like the output of aggregate_spatial."""
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [0.14, 42.83]},
                "properties": {
                    "name": "a",
                    "values": {T1: numpy.float32(0.25), T2: numpy.ma.masked},
                },
            },
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [0.15, 42.84]},
                "properties": {"values": {T1: numpy.float64("nan"), T2: 3}},
            },
        ],
    }


@pytest.mark.parametrize(
    "name,expected",
    [
        ("GTiff", "GTiff"),
        ("gtiff", "GTiff"),
        ("TIFF", "GTiff"),
        ("png", "PNG"),
        ("jpg", "JPEG"),
        ("geojson", "GeoJSON"),
        ("Json", "JSON"),
        ("csv", "CSV"),
        ("plain", "txt"),
        ("metajson", "metajson"),
    ],
)
def test_get_output_format(name, expected):
    """Format names and aliases are case-insensitive."""
    assert get_output_format(name).name == expected


def test_get_output_format_unknown():
    """An unknown format raises FormatUnsuitable and lists the formats."""
    with pytest.raises(FormatUnsuitable, match="GeoJSON") as exc_info:
        get_output_format("webp")
    assert exc_info.value.code == "FormatUnsuitable"
    assert exc_info.value.status_code == 400


def test_data_kind(vector_cube):
    """The kind of data selects the encoder."""
    image = ImageData(numpy.zeros((1, 2, 2), dtype="uint8"))
    assert data_kind(image) == "raster"
    assert data_kind(image.array) == "raster"
    assert data_kind(RasterStack.from_images({T1: image})) == "raster"
    assert data_kind(vector_cube) == "vector"
    assert data_kind({"a": 1}) == "other"
    assert data_kind(1.5) == "other"


def test_to_json_safe():
    """Dates, numpy scalars, masked and non-finite values become JSON types."""
    data = {
        T1: {"value": numpy.float32(0.5), "total_count": numpy.int64(4)},
        "masked": numpy.ma.masked,
        "nan": float("nan"),
        "inf": numpy.float64("inf"),
        "array": numpy.ma.array([1, 2], mask=[False, True]),
        "tuple": (1, 2),
        3: "int key",
    }
    assert _to_json_safe(data) == {
        "2023-01-05T10:56:21+00:00": {"value": 0.5, "total_count": 4},
        "masked": None,
        "nan": None,
        "inf": None,
        "array": [1, None],
        "tuple": [1, 2],
        "3": "int key",
    }


@pytest.mark.parametrize(
    "format,media_type",
    [("JSON", "application/json"), ("GeoJSON", "application/geo+json")],
)
def test_encode_vector_cube_json(vector_cube, format, media_type):
    """A vector cube from aggregate_spatial is written as JSON and GeoJSON."""
    result = encode(vector_cube, format)
    assert result.media_type == media_type

    collection = json.loads(result.data)
    assert collection["type"] == "FeatureCollection"
    assert collection["features"][0]["properties"]["values"] == {
        "2023-01-05T10:56:21+00:00": 0.25,
        "2023-01-10T10:56:21+00:00": None,
    }
    assert collection["features"][1]["properties"]["values"] == {
        "2023-01-05T10:56:21+00:00": None,
        "2023-01-10T10:56:21+00:00": 3,
    }


def test_encode_vector_cube_csv(vector_cube):
    """CSV dates are the same ISO 8601 strings as in the JSON output."""
    result = encode(vector_cube, "CSV")
    assert result.media_type == "text/csv"
    lines = result.data.decode().splitlines()
    assert lines[0] == "date,feature_index,value"
    assert lines[1:] == [
        "2023-01-05T10:56:21+00:00,0,0.25",
        "2023-01-10T10:56:21+00:00,0,",
        "2023-01-05T10:56:21+00:00,1,",
        "2023-01-10T10:56:21+00:00,1,3",
    ]


@pytest.mark.parametrize("format", ["PNG", "GTiff", "metajson"])
def test_encode_vector_cube_raster_format(vector_cube, format):
    """A raster format can't store a vector cube."""
    with pytest.raises(FormatUnsuitable, match="vector data"):
        encode(vector_cube, format)


def test_encode_raster_vector_format():
    """A vector format can't store a raster."""
    image = ImageData(numpy.zeros((1, 2, 2), dtype="uint8"))
    with pytest.raises(FormatUnsuitable, match="raster data"):
        encode(image, "GeoJSON")


def test_encode_result(vector_cube):
    """A result without save_result gets a default format."""
    encoded = SaveResultData(data=b"x", media_type="image/png")
    assert encode_result(encoded) is encoded

    assert encode_result(vector_cube).media_type == "application/json"
    assert encode_result({"a": 1}).data == b'{"a": 1}'
    assert encode_result(1.5).data == b"1.5"
    assert encode_result("red").media_type == "text/plain"

    image = ImageData(numpy.zeros((1, 2, 2), dtype="uint8"))
    assert encode_result(image).media_type == "image/tiff"
    assert encode_result(image, raster_format="PNG").media_type == "image/png"


def test_file_formats_document():
    """/file_formats lists the visible formats, with valid openEO documents."""
    document = file_formats_document()
    FileFormats.model_validate(document)

    output = document["output"]
    assert set(output) == {"GTiff", "PNG", "JPEG", "JSON", "GeoJSON", "CSV"}
    assert output["GTiff"]["gis_data_types"] == ["raster"]
    assert output["GeoJSON"]["gis_data_types"] == ["vector"]
    assert output["JSON"]["gis_data_types"] == ["vector", "other"]


def test_every_format_has_an_encoder():
    """Each registered format can encode at least one kind of data."""
    for fmt in OUTPUT_FORMATS:
        assert fmt.encoders, fmt.name
        assert fmt.media_type, fmt.name


def test_file_formats_endpoint(app_with_auth):
    """GET /file_formats lists the vector formats."""
    response = app_with_auth.get("/file_formats")
    assert response.status_code == 200
    assert {"JSON", "GeoJSON", "CSV"} <= set(response.json()["output"])


def test_result_without_save_result(app_with_auth):
    """POST /result without save_result returns a vector cube as JSON (#439)."""
    collection = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [0.14, 42.83]},
                "properties": {"values": {"2023-01-05": 0.25}},
            }
        ],
    }
    process_graph = {
        "process": {
            "process_graph": {
                "get_item": {
                    "process_id": "get_param_item",
                    "arguments": {"parameter": {"fc": collection}, "path": "$.fc"},
                    "result": True,
                }
            }
        }
    }

    response = app_with_auth.post("/result", json=process_graph)
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json"
    assert response.json() == collection


@pytest.mark.parametrize(
    "save_format,media_type",
    [
        (None, "image/png"),
        ("png", "image/png"),
        ("gtiff", "image/tiff"),
        ("jpg", "image/jpeg"),
    ],
)
def test_xyz_tile_media_type(app_with_auth, save_format, media_type):
    """XYZ tiles take the media type from the encoded result.

    Format names are case-insensitive, and a graph without save_result gives PNG.
    """

    @process
    def test_tile_image() -> ImageData:
        return ImageData(
            numpy.ma.array(numpy.full((1, 256, 256), 7, dtype="uint8")),
            bounds=(-180, -85, 180, 85),
            crs="epsg:4326",
        )

    app_with_auth.app.endpoints.process_registry[None]["test_tile_image"] = Process(
        implementation=test_tile_image,
        spec={"id": "test_tile_image", "parameters": []},
    )

    process_graph = {
        "image": {"process_id": "test_tile_image", "arguments": {}, "result": True}
    }
    if save_format:
        process_graph["image"].pop("result")
        process_graph["save"] = {
            "process_id": "save_result",
            "arguments": {"data": {"from_node": "image"}, "format": save_format},
            "result": True,
        }

    response = app_with_auth.post(
        "/services",
        json={
            "process": {"process_graph": process_graph},
            "type": "xyz",
            "configuration": {},
        },
    )
    assert response.status_code == 201, response.text
    service_id = response.headers["location"].split("/")[-1]

    tile = app_with_auth.get(f"/services/xyz/{service_id}/tiles/0/0/0")
    assert tile.status_code == 200, tile.text
    assert tile.headers["content-type"] == media_type
    assert len(tile.content) > 0


def test_encode_multiband_vector_cube_csv():
    """A multi-band vector cube gives one CSV column per band."""
    cube = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [0.14, 42.83]},
                "properties": {
                    "values": {
                        T1: {"B03": numpy.float32(0.25), "B04": numpy.ma.masked},
                        T2: {"B03": 1, "B04": 2},
                    }
                },
            }
        ],
    }
    lines = encode(cube, "CSV").data.decode().splitlines()
    assert lines == [
        "date,feature_index,B03,B04",
        "2023-01-05T10:56:21+00:00,0,0.25,",
        "2023-01-10T10:56:21+00:00,0,1,2",
    ]


def test_encode_empty_vector_cube_csv():
    """A vector cube without values gives only the header."""
    cube = {"type": "FeatureCollection", "features": []}
    assert encode(cube, "CSV").data.decode().splitlines() == [
        "date,feature_index,value"
    ]
