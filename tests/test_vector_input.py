"""Tests for load_geojson, vector load_url and the /file_formats input (ADR 0014)."""

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pytest
from rasterio.crs import CRS

from titiler.openeo.errors import (
    FormatUnsuitable,
    ProcessParameterInvalid,
    VectorFeatureLimitExceeded,
)
from titiler.openeo.processes.implementations import formats, io
from titiler.openeo.processes.implementations.core import process
from titiler.openeo.processes.implementations.formats import (
    encode,
    file_formats_document,
    get_input_format,
)
from titiler.openeo.processes.implementations.io import load_url
from titiler.openeo.processes.implementations.vector import load_geojson
from titiler.openeo.processes.implementations.vector_model import VectorCube

UTM = CRS.from_epsg(32631)
D1 = datetime(2023, 1, 5)
SERVER = Path(__file__).parent / "fixtures" / "range_http_server.py"

FEATURES = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [1.0, 2.0]},
            "properties": {"a": 1, "b": 2.5, "name": "x", "arr": [1, 2, 3]},
        },
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [3.0, 4.0]},
            "properties": {"a": None, "name": "y", "arr": [4]},
        },
    ],
}


def _utm_cube() -> VectorCube:
    """Two points in UTM, with a date, an int, a text with a null and a value."""
    return VectorCube(
        geometries=[
            {"type": "Point", "coordinates": [500000.0, 10.0]},
            {"type": "Point", "coordinates": [500010.0, 20.0]},
        ],
        crs=UTM,
        properties=[{"name": "a", "n": 1}, {"name": None, "n": 2}],
        values=np.ma.masked_array(np.array([[1.5], [2.5]]), mask=[[False], [True]]),
        dims=("geometry", "t"),
        labels={"t": [D1]},
    )


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    """Serve a folder over HTTP with range requests, in another process."""
    folder = tmp_path_factory.mktemp("http")
    proc = subprocess.Popen(
        [sys.executable, str(SERVER), str(folder)], stdout=subprocess.PIPE, text=True
    )
    try:
        port = proc.stdout.readline().strip()
        yield folder, f"http://127.0.0.1:{port}"
    finally:
        proc.kill()
        proc.wait()


def _publish(server, name: str, data: bytes) -> str:
    folder, base = server
    (folder / name).write_bytes(data)
    return f"{base}/{name}"


# load_url: vector formats


@pytest.mark.parametrize(
    "fmt,name",
    [
        ("GPKG", "r.gpkg"),
        ("FlatGeobuf", "r.fgb"),
        ("GeoParquet", "r.parquet"),
        ("ESRI Shapefile", "r.shp.zip"),
    ],
)
def test_load_url_reads_our_output(server, fmt, name):
    """A file from save_result comes back with its CRS, geometries and columns."""
    url = _publish(server, name, encode(_utm_cube(), fmt).data)
    cube = load_url(url, fmt)

    assert isinstance(cube, VectorCube)
    assert cube.dims == ("geometry",)
    assert cube.crs == UTM
    # FlatGeobuf sorts the features for its spatial index: sort them by `n`.
    rows = sorted(zip(cube.geometries, cube.properties), key=lambda r: r[1]["n"])
    assert [tuple(g["coordinates"]) for g, _ in rows] == [
        (500000.0, 10.0),
        (500010.0, 20.0),
    ]
    first, second = [p for _, p in rows]
    assert first["name"] == "a" and second["name"] is None
    assert [first["n"], second["n"]] == [1, 2]
    assert first["value"] == 1.5 and second["value"] is None
    # Shapefile stores dates as text.
    assert first["t"] in (D1, "2023-01-05T00:00:00")


def test_load_url_geojson(server):
    """GeoJSON is read without the extra, in EPSG:4326, with its properties."""
    url = _publish(server, "r.geojson", json.dumps(FEATURES).encode())
    cube = load_url(url, "geojson")

    assert cube.crs == CRS.from_epsg(4326)
    assert cube.geometries[1] == {"type": "Point", "coordinates": [3.0, 4.0]}
    assert cube.properties[0]["arr"] == [1, 2, 3]


def test_load_url_shapefile_without_zip(server):
    """A `.shp` URL reads the `.dbf`, `.shx` and `.prj` files next to it."""
    import zipfile
    from io import BytesIO

    folder, base = server
    archive = zipfile.ZipFile(BytesIO(encode(_utm_cube(), "shp").data))
    archive.extractall(folder / "unzipped")
    shp = next(n for n in archive.namelist() if n.endswith(".shp"))

    cube = load_url(f"{base}/unzipped/{shp}", "ESRI Shapefile")
    assert cube.crs == UTM
    assert cube.properties[0]["name"] == "a"


def test_load_url_gpkg_layer_name(server):
    """The option `layer_name` selects the layer of a GeoPackage."""
    data = encode(_utm_cube(), "GPKG", {"layer_name": "zones"}).data
    url = _publish(server, "layers.gpkg", data)
    assert len(load_url(url, "GPKG", {"layer_name": "zones"})) == 2
    with pytest.raises(FormatUnsuitable, match="zones2"):
        load_url(url, "GPKG", {"layer_name": "zones2"})


def test_load_url_then_save_result(server):
    """A loaded file can be saved again: load_url -> save_result(GeoJSON)."""
    url = _publish(server, "again.fgb", encode(_utm_cube(), "FlatGeobuf").data)
    document = json.loads(encode(load_url(url, "fgb"), "GeoJSON").data)
    (feature,) = [f for f in document["features"] if f["properties"]["n"] == 1]
    lon, lat = feature["geometry"]["coordinates"]
    assert lon == pytest.approx(3.0, abs=1e-3)
    assert feature["properties"]["name"] == "a"


# load_url: errors


@pytest.mark.parametrize("fmt", ["GeoJSON", "GPKG", "GeoParquet"])
def test_load_url_feature_limit(server, monkeypatch, fmt):
    """More features than max_vector_features gives OutputLimitExceeded."""
    monkeypatch.setenv("TITILER_OPENEO_PROCESSING_MAX_VECTOR_FEATURES", "1")
    data = (
        json.dumps(FEATURES).encode()
        if fmt == "GeoJSON"
        else encode(_utm_cube(), fmt).data
    )
    url = _publish(server, f"limit.{fmt.lower()}", data)
    with pytest.raises(VectorFeatureLimitExceeded) as error:
        load_url(url, fmt)
    assert error.value.code == "OutputLimitExceeded"


def test_load_url_download_limit(server, monkeypatch):
    """A downloaded file above max_vector_download_bytes is refused."""
    monkeypatch.setenv("TITILER_OPENEO_PROCESSING_MAX_VECTOR_DOWNLOAD_BYTES", "10")
    url = _publish(server, "big.geojson", json.dumps(FEATURES).encode())
    with pytest.raises(ProcessParameterInvalid, match="larger than 10 bytes"):
        load_url(url, "GeoJSON")


@pytest.mark.parametrize(
    "fmt,data",
    [
        ("GeoJSON", b"not json"),
        ("GeoJSON", b"[1, 2]"),
        ("GeoParquet", b"not parquet"),
        ("GeoParquet", b"PAR1 truncated PAR1"),
        ("GPKG", b"not a geopackage"),
    ],
)
def test_load_url_not_the_format(server, fmt, data):
    """A file that is not in the format gives FormatUnsuitable."""
    url = _publish(server, f"bad.{fmt.lower()}", data)
    with pytest.raises(FormatUnsuitable, match=f"as {fmt}"):
        load_url(url, fmt)


def test_load_url_not_found(server):
    """An HTTP error gives a clear 400 error."""
    _, base = server
    with pytest.raises(ProcessParameterInvalid, match="404"):
        load_url(f"{base}/missing.geojson", "GeoJSON")
    with pytest.raises(FormatUnsuitable):
        load_url(f"{base}/missing.gpkg", "GPKG")


@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://example.com/a.gpkg"])
def test_load_url_http_only(url):
    """Vector files are read from HTTP and HTTPS URLs only."""
    with pytest.raises(ProcessParameterInvalid, match="HTTP and HTTPS"):
        load_url(url, "GeoJSON")


def test_load_url_signed_url_is_not_in_errors(server, monkeypatch, recwarn):
    """The reader uses the signed URL, but an error shows the URL of the user."""
    monkeypatch.setattr(
        io, "get_signer", lambda key: lambda href: f"{href}?token=secret"
    )
    url = _publish(server, "signed.gpkg", encode(_utm_cube(), "GPKG").data)
    assert len(load_url(url, "GPKG")) == 2

    _, base = server
    for fmt, name in [("GPKG", "missing.gpkg"), ("GeoJSON", "missing.json")]:
        with pytest.raises((FormatUnsuitable, ProcessParameterInvalid)) as error:
            load_url(f"{base}/{name}", fmt)
        assert "secret" not in str(error.value.message)
    assert not [w for w in recwarn if "secret" in str(w.message)]


# GeoParquet written by other tools


def _geoparquet(server, name: str, geo: dict, extra_columns=None) -> str:
    from arro3.core import Array, DataType, Table
    from arro3.io import write_parquet
    from shapely import Point, to_wkb

    columns = {
        "geometry": Array(
            [to_wkb(Point(1, 2)), to_wkb(Point(3, 4))], DataType.binary()
        ),
        "id": Array([1, 2], DataType.int64()),
        **(extra_columns or {}),
    }
    folder, base = server
    write_parquet(
        Table.from_pydict(columns),
        str(folder / name),
        key_value_metadata={"geo": json.dumps(geo)},
        skip_arrow_metadata=True,
    )
    return f"{base}/{name}"


def test_geoparquet_without_crs_is_crs84(server):
    """No `crs` in the column metadata means OGC:CRS84."""
    geo = {
        "version": "1.1.0",
        "primary_column": "geometry",
        "columns": {"geometry": {"encoding": "WKB", "geometry_types": []}},
    }
    cube = load_url(_geoparquet(server, "nocrs.parquet", geo), "GeoParquet")
    assert cube.crs == CRS.from_epsg(4326)
    assert [p["id"] for p in cube.properties] == [1, 2]


def test_geoparquet_covering_columns_are_not_properties(server):
    """The bbox covering columns of GeoParquet 1.1 are not properties."""
    from arro3.core import Array, DataType

    geo = {
        "version": "1.1.0",
        "primary_column": "geometry",
        "columns": {
            "geometry": {
                "encoding": "WKB",
                "geometry_types": ["Point"],
                "covering": {
                    "bbox": {
                        "xmin": ["xmin"],
                        "ymin": ["ymin"],
                        "xmax": ["xmax"],
                        "ymax": ["ymax"],
                    }
                },
            }
        },
    }
    extra = {
        key: Array([1.0, 3.0], DataType.float64())
        for key in ("xmin", "ymin", "xmax", "ymax")
    }
    cube = load_url(_geoparquet(server, "cover.parquet", geo, extra), "GeoParquet")
    assert cube.properties == [{"id": 1}, {"id": 2}]


@pytest.mark.parametrize(
    "geo,match",
    [
        ({"version": "1.1.0"}, "no valid `geo` metadata"),
        (
            {
                "version": "1.1.0",
                "primary_column": "geometry",
                "columns": {"geometry": {"encoding": "point"}},
            },
            "WKB encoding only",
        ),
    ],
)
def test_geoparquet_bad_metadata(server, geo, match):
    """Missing `geo` metadata or a native (GeoArrow) encoding is refused."""
    with pytest.raises(FormatUnsuitable, match=match):
        load_url(_geoparquet(server, "bad_geo.parquet", geo), "GeoParquet")


# Input formats


def test_input_format_names():
    """Names and aliases are case-insensitive. An unknown name is a raster."""
    assert get_input_format("cog").name == "GTiff"
    assert get_input_format("GEOPACKAGE").name == "GPKG"
    assert get_input_format("shp").name == "ESRI Shapefile"
    assert get_input_format("Parquet").name == "GeoParquet"
    assert get_input_format("netCDF") is None


def test_file_formats_input_section():
    """/file_formats lists the input formats."""
    document = file_formats_document()
    assert set(document["input"]) == {
        "GTiff",
        "GeoJSON",
        "GeoParquet",
        "GPKG",
        "FlatGeobuf",
        "ESRI Shapefile",
    }
    assert document["input"]["GTiff"]["gis_data_types"] == ["raster"]
    assert document["input"]["GPKG"]["gis_data_types"] == ["vector"]
    assert "layer_name" in document["input"]["GPKG"]["parameters"]


def test_without_the_vector_extra(server, monkeypatch):
    """Without the extra, GeoJSON and GTiff stay; the other formats go."""
    monkeypatch.setattr(formats, "_module_available", lambda name: False)
    assert set(file_formats_document()["input"]) == {"GTiff", "GeoJSON"}
    with pytest.raises(FormatUnsuitable, match=r"titiler-openeo\[vector\]"):
        load_url("https://example.com/a.gpkg", "GPKG")

    url = _publish(server, "core.geojson", json.dumps(FEATURES).encode())
    assert len(load_url(url, "GeoJSON")) == 2


# load_geojson


def test_load_geojson_keeps_properties():
    """Without `properties`, the cube has the geometry dimension only."""
    cube = load_geojson(FEATURES)
    assert cube.dims == ("geometry",)
    assert cube.values is None
    assert cube.crs == CRS.from_epsg(4326)
    assert cube.properties[0] == {"a": 1, "b": 2.5, "name": "x", "arr": [1, 2, 3]}


@pytest.mark.parametrize(
    "names,labels,values,dtype",
    [
        (["a"], ["a"], [[1], [None]], np.int64),
        (["a", "b"], ["a", "b"], [[1.0, 2.5], [None, None]], np.float64),
        (["arr"], [0, 1, 2], [[1, 2, 3], [4, None, None]], np.int64),
        (["name"], ["name"], [["x"], ["y"]], object),
    ],
)
def test_load_geojson_properties_dimension(names, labels, values, dtype):
    """The listed properties make the `properties` dimension (openEO spec)."""
    cube = load_geojson(FEATURES, names)

    assert cube.dims == ("geometry", "properties")
    assert cube.labels == {"properties": labels}
    assert cube.values.tolist() == values
    assert cube.values.dtype == dtype
    # The properties of the dimension are not repeated as feature properties.
    assert not set(names) & set(cube.properties[0])


def test_load_geojson_geometry_and_feature():
    """A bare geometry or one feature is a cube of one geometry."""
    point = {"type": "Point", "coordinates": [1, 2]}
    assert load_geojson(point).geometries == [point]
    assert len(load_geojson(FEATURES["features"][0])) == 1


@pytest.mark.parametrize(
    "data,names,match",
    [
        ([1, 2], None, "GeoJSON object"),
        (
            {"type": "GeometryCollection", "geometries": []},
            None,
            "GeometryCollection",
        ),
        (FEATURES, ["a", "arr"], "each value must be a scalar"),
        (FEATURES, ["a", "a"], "unique"),
    ],
)
def test_load_geojson_errors(data, names, match):
    """Invalid input gives ProcessParameterInvalid."""
    with pytest.raises(ProcessParameterInvalid, match=match):
        load_geojson(data, names)


def test_load_geojson_feature_limit(monkeypatch):
    """More geometries than max_vector_features gives OutputLimitExceeded."""
    monkeypatch.setenv("TITILER_OPENEO_PROCESSING_MAX_VECTOR_FEATURES", "1")
    with pytest.raises(VectorFeatureLimitExceeded):
        load_geojson(FEATURES)


def test_load_geojson_to_gpkg_columns(tmp_path):
    """The properties dimension becomes one column for each label."""
    import pyogrio.raw

    path = tmp_path / "properties.gpkg"
    path.write_bytes(encode(load_geojson(FEATURES, ["arr"]), "GPKG").data)
    meta, _, _, fields = pyogrio.raw.read(path)
    assert list(meta["fields"]) == ["a", "b", "name", "0", "1", "2"]
    assert fields[3].tolist() == [1, 4]


def test_load_geojson_as_polygon_mask():
    """load_geojson -> mask_polygon: the decorator gives the FeatureCollection."""
    from rio_tiler.models import ImageData

    from titiler.openeo.processes.implementations.data_model import RasterStack
    from titiler.openeo.processes.implementations.spatial import mask_polygon

    image = ImageData(
        np.ma.ones((1, 4, 4), dtype="uint8"),
        crs=CRS.from_epsg(4326),
        bounds=(0, 0, 4, 4),
    )
    left_half = process(load_geojson)(
        data={
            "type": "Polygon",
            "coordinates": [[[0, 0], [2, 0], [2, 4], [0, 4], [0, 0]]],
        }
    )
    assert isinstance(left_half, VectorCube)

    result = process(mask_polygon)(
        data=RasterStack.from_images({D1: image}), mask=left_half
    )
    mask = np.ma.getmaskarray(result[D1].array[0])
    assert not mask[:, :2].any()
    assert mask[:, 2:].all()


def test_process_graph_load_geojson_to_geojson():
    """A process graph loads GeoJSON with a properties dimension and saves it."""
    from openeo_pg_parser_networkx import OpenEOProcessGraph

    from titiler.openeo.processes import process_registry

    graph = {
        "load": {
            "process_id": "load_geojson",
            "arguments": {"data": FEATURES, "properties": ["a", "b"]},
        },
        "save": {
            "process_id": "save_result",
            "arguments": {"data": {"from_node": "load"}, "format": "GeoJSON"},
            "result": True,
        },
    }
    run = OpenEOProcessGraph(pg_data={"process_graph": graph}).to_callable(
        process_registry=process_registry
    )
    document = json.loads(run().data)
    assert document["features"][0]["properties"]["values"] == {"a": 1.0, "b": 2.5}
    assert document["features"][1]["properties"]["values"] == {"a": None, "b": None}
