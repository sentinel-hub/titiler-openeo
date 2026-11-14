"""Tests for the vector output formats of the `vector` extra (ADR 0014 §2.5)."""

import io
import json
import zipfile
from datetime import datetime

import numpy as np
import pytest
from rasterio.crs import CRS
from rio_tiler.models import ImageData

from titiler.openeo.errors import FormatUnsuitable, ProcessParameterInvalid
from titiler.openeo.processes.implementations import formats
from titiler.openeo.processes.implementations.data_model import RasterStack
from titiler.openeo.processes.implementations.formats import (
    encode,
    file_formats_document,
)
from titiler.openeo.processes.implementations.spatial import aggregate_spatial
from titiler.openeo.processes.implementations.vector import raster_to_vector
from titiler.openeo.processes.implementations.vector_model import VectorCube

pyogrio = pytest.importorskip("pyogrio")
arro3_io = pytest.importorskip("arro3.io")

D1, D2 = datetime(2023, 1, 5), datetime(2023, 2, 1)
UTM = CRS.from_epsg(32631)
ZONES = np.array([[1, 1, 2, 2]] * 4, dtype="uint8")
SQUARE = {
    "type": "Polygon",
    "coordinates": [[[0.0, 0.0], [2.0, 0.0], [2.0, 2.0], [0.0, 2.0], [0.0, 0.0]]],
}
OGR_FORMATS = ["GPKG", "FlatGeobuf", "ESRI Shapefile"]


def _img(array, crs=CRS.from_epsg(4326), bounds=(0, 0, 4, 4), mask=None, bands=None):
    data = array if array.ndim == 3 else array[np.newaxis]
    return ImageData(
        np.ma.array(data, mask=np.zeros(data.shape, bool) if mask is None else mask),
        band_descriptions=bands or [f"b{i + 1}" for i in range(data.shape[0])],
        crs=crs,
        bounds=bounds,
    )


@pytest.fixture
def zones_cube() -> VectorCube:
    """Two polygons in EPSG:32631, from raster_to_vector."""
    return raster_to_vector(
        RasterStack.from_images(
            {D1: _img(ZONES, crs=UTM, bounds=(500000, 0, 500040, 40))}
        )
    )


@pytest.fixture
def stats_cube() -> VectorCube:
    """A two-band, two-date cube from aggregate_spatial, with one masked band."""
    mask = np.zeros((2, 4, 4), bool)
    mask[1] = True
    stack = RasterStack.from_images(
        {
            D1: _img(
                np.stack([np.full((4, 4), 1.5), np.full((4, 4), 3.0)]),
                mask=mask,
                bands=["B03", "B04"],
            ),
            D2: _img(
                np.stack([np.full((4, 4), 2.5), np.full((4, 4), 4.0)]),
                bands=["B03", "B04"],
            ),
        }
    )
    return aggregate_spatial(
        data=stack,
        geometries={
            "type": "Feature",
            "geometry": SQUARE,
            "properties": {"name": "field", "tags": ["a", "b"]},
        },
        reducer=lambda data, **kwargs: np.ma.mean(data),
    )


def _ogr_file(data: bytes, fmt: str, tmp_path) -> str:
    """Write an OGR output to a file with the right extension; return its path."""
    if fmt == "ESRI Shapefile":
        with zipfile.ZipFile(io.BytesIO(data)) as zipped:
            zipped.extractall(tmp_path)
        return str(tmp_path / "result.shp")
    path = tmp_path / ("result.gpkg" if fmt == "GPKG" else "result.fgb")
    path.write_bytes(data)
    return str(path)


def _read_ogr(data: bytes, fmt: str, tmp_path):
    """Read the first layer of an OGR output; return (metadata, geometries, fields)."""
    meta, _, geometries, fields = pyogrio.raw.read(_ogr_file(data, fmt, tmp_path))
    return meta, geometries, dict(zip(meta["fields"].tolist(), fields, strict=True))


def _read_parquet(data: bytes):
    """Read a Parquet output; return (columns as Python lists, geo metadata)."""
    table = arro3_io.read_parquet(io.BytesIO(data)).read_all()
    geo = json.loads(table.schema.metadata[b"geo"])
    return {name: table[name].to_pylist() for name in table.column_names}, geo


class TestRecords:
    """VectorCube.to_records: the table layout of the GIS formats."""

    def test_dates_and_bands(self, stats_cube):
        """One row for each geometry and date; one column for each band."""
        records = stats_cube.to_records()
        assert list(records.columns) == ["name", "tags", "t", "B03", "B04"]
        assert records.columns["t"] == [D1, D2]
        assert records.columns["B03"] == [1.5, 2.5]
        assert records.columns["B04"] == [None, 4.0]
        # A list property is JSON text.
        assert records.columns["tags"] == ['["a", "b"]'] * 2
        assert len(records.geometries) == 2

    def test_target_dimension(self):
        """Each band and statistic is a column."""
        cube = aggregate_spatial(
            data=RasterStack.from_images({D1: _img(ZONES.astype("float32"))}),
            geometries=SQUARE,
            reducer=lambda data, **kwargs: np.ma.mean(data),
            target_dimension="stats",
        )
        assert list(cube.to_records().columns) == [
            "t",
            "value",
            "total_count",
            "valid_count",
        ]

    def test_geometry_only_dimension(self, zones_cube):
        """A cube with the geometry dimension only has a `value` column."""
        records = zones_cube.to_records()
        assert sorted(records.columns["value"]) == [1, 2]
        assert records.crs == UTM

    def test_name_collision(self):
        """A property with the name of a value column gets a suffix."""
        cube = VectorCube(
            geometries=[SQUARE],
            properties=[{"value": "label", "geometry": 1}],
            values=np.ma.array([7]),
        )
        records = cube.to_records(reserved=("geometry",))
        assert records.columns == {
            "value": ["label"],
            "geometry_1": [1],
            "value_1": [7],
        }


@pytest.mark.parametrize("fmt", OGR_FORMATS)
def test_ogr_formats(fmt, zones_cube, tmp_path):
    """The OGR formats keep the CRS, the geometries and the values."""
    result = encode(zones_cube, fmt)
    meta, geometries, fields = _read_ogr(result.data, fmt, tmp_path)

    assert CRS.from_user_input(meta["crs"]) == UTM
    assert len(geometries) == 2
    assert sorted(fields["value"].tolist()) == [1, 2]
    assert result.filename.startswith("result.")


@pytest.mark.parametrize("fmt", OGR_FORMATS)
def test_ogr_dates_and_nulls(fmt, stats_cube, tmp_path):
    """A masked value is null; the date is a datetime (text in a Shapefile)."""
    _, _, fields = _read_ogr(encode(stats_cube, fmt).data, fmt, tmp_path)
    assert np.isnan(fields["B04"][0])
    assert fields["B04"][1] == 4.0
    if fmt == "ESRI Shapefile":
        assert fields["t"][0] == "2023-01-05T00:00:00"
    else:
        assert fields["t"][0] == np.datetime64("2023-01-05T00:00:00")


def test_shapefile_zip_content(zones_cube):
    """The zip has the Shapefile parts, with the encoding in the .cpg file."""
    result = encode(zones_cube, "shp", {"encoding": "ISO-8859-1"})
    assert result.media_type == "application/zip"
    assert result.filename == "result.shp.zip"
    with zipfile.ZipFile(io.BytesIO(result.data)) as zipped:
        names = set(zipped.namelist())
        assert {"result.shp", "result.shx", "result.dbf", "result.prj"} <= names
        assert zipped.read("result.cpg").decode().strip() == "ISO-8859-1"


def test_gpkg_layer_name(zones_cube, tmp_path):
    """The GPKG option `layer_name` names the layer."""
    data = encode(zones_cube, "GPKG", {"layer_name": "zones"}).data
    assert pyogrio.list_layers(_ogr_file(data, "GPKG", tmp_path))[0][0] == "zones"


def test_geoparquet(stats_cube):
    """GeoParquet 1.1: WKB geometries, CRS as PROJJSON, a timestamp date column."""
    result = encode(stats_cube, "GeoParquet")
    assert result.media_type == "application/vnd.apache.parquet"
    assert result.filename == "result.parquet"

    columns, geo = _read_parquet(result.data)
    assert geo["version"] == "1.1.0"
    assert geo["primary_column"] == "geometry"
    column = geo["columns"]["geometry"]
    assert column["encoding"] == "WKB"
    assert column["geometry_types"] == ["Polygon"]
    assert column["crs"]["id"] == {"authority": "EPSG", "code": 4326}
    assert column["bbox"] == [0.0, 0.0, 2.0, 2.0]

    assert columns["t"] == [D1, D2]
    assert columns["B04"] == [None, 4.0]
    assert columns["name"] == ["field", "field"]
    assert all(isinstance(g, bytes) for g in columns["geometry"])


def test_geoparquet_projected_crs(zones_cube):
    """GeoParquet keeps the CRS of the cube."""
    _, geo = _read_parquet(encode(zones_cube, "GeoParquet").data)
    assert geo["columns"]["geometry"]["crs"]["id"]["code"] == 32631
    # `Parquet` (the name at other openEO back-ends) is an alias.
    assert encode(zones_cube, "parquet").media_type == "application/vnd.apache.parquet"


@pytest.mark.parametrize("compression", ["snappy", "zstd", "gzip", "uncompressed"])
def test_geoparquet_compression(compression, zones_cube):
    """The compression codecs give a file that can be read."""
    data = encode(zones_cube, "GeoParquet", {"compression": compression}).data
    columns, _ = _read_parquet(data)
    assert sorted(columns["value"]) == [1, 2]


def test_geoparquet_bad_compression(zones_cube):
    """An unknown codec is an invalid option."""
    with pytest.raises(ProcessParameterInvalid, match="compression"):
        encode(zones_cube, "GeoParquet", {"compression": "bogus"})


def test_mixed_geometry_types(tmp_path):
    """GPKG takes mixed types; a Shapefile stores one type only."""
    cube = VectorCube.from_geojson(
        {
            "type": "FeatureCollection",
            "features": [
                {"type": "Feature", "geometry": SQUARE, "properties": {}},
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [1, 1]},
                    "properties": {},
                },
            ],
        }
    )
    _, geometries, _ = _read_ogr(encode(cube, "GPKG").data, "GPKG", tmp_path)
    assert len(geometries) == 2
    with pytest.raises(FormatUnsuitable, match="one geometry type"):
        encode(cube, "ESRI Shapefile")


def test_polygon_and_multipolygon_are_promoted(tmp_path):
    """Polygon and MultiPolygon go in one MultiPolygon layer."""
    multi = {"type": "MultiPolygon", "coordinates": [SQUARE["coordinates"]]}
    cube = VectorCube(geometries=[SQUARE, multi])
    meta, _, _ = _read_ogr(
        encode(cube, "ESRI Shapefile").data, "ESRI Shapefile", tmp_path
    )
    assert meta["geometry_type"] in ("MultiPolygon", "Polygon")
    assert len(_read_ogr(encode(cube, "GPKG").data, "GPKG", tmp_path)[1]) == 2


def test_feature_collection_dict():
    """A GeoJSON FeatureCollection (not a VectorCube) is written too."""
    collection = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "geometry": SQUARE, "properties": {"name": "a"}}
        ],
    }
    columns, _ = _read_parquet(encode(collection, "GeoParquet").data)
    assert columns["name"] == ["a"]


def test_formats_without_the_extra(monkeypatch, zones_cube):
    """Without the extra, the formats are not listed and give an install hint."""
    monkeypatch.setattr(formats, "_module_available", lambda name: name != "pyogrio")

    output = file_formats_document()["output"]
    assert "GPKG" not in output
    assert "GeoJSON" in output
    with pytest.raises(FormatUnsuitable, match=r"titiler-openeo\[vector\]"):
        encode(zones_cube, "GPKG")


def test_geojson_has_no_attachment(zones_cube):
    """The text formats are not sent as a file download."""
    assert encode(zones_cube, "GeoJSON").filename is None


def test_result_endpoint_sends_a_file(app_with_auth, tmp_path):
    """POST /result gives the media type and the file name of the format."""
    collection = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "geometry": SQUARE, "properties": {"name": "a"}}
        ],
    }
    graph = {
        "process": {
            "process_graph": {
                "get": {
                    "process_id": "get_param_item",
                    "arguments": {"parameter": {"fc": collection}, "path": "$.fc"},
                },
                "save": {
                    "process_id": "save_result",
                    "arguments": {"data": {"from_node": "get"}, "format": "GPKG"},
                    "result": True,
                },
            }
        }
    }
    response = app_with_auth.post("/result", json=graph)
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/geopackage+sqlite3"
    assert (
        response.headers["content-disposition"] == 'attachment; filename="result.gpkg"'
    )
    assert len(pyogrio.raw.read(_ogr_file(response.content, "GPKG", tmp_path))[2]) == 1
