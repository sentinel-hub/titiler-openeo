"""Tests for the vector data cube (ADR 0014)."""

import json
from datetime import datetime
from typing import Any, Dict

import numpy as np
import pytest
from rasterio.crs import CRS
from rio_tiler.models import ImageData

from titiler.openeo.errors import ProcessParameterInvalid
from titiler.openeo.processes.implementations.core import process
from titiler.openeo.processes.implementations.data_model import RasterStack
from titiler.openeo.processes.implementations.formats import (
    data_kind,
    encode,
    encode_result,
)
from titiler.openeo.processes.implementations.spatial import (
    aggregate_spatial,
    mask_polygon,
)
from titiler.openeo.processes.implementations.vector_model import VectorCube

SQUARE = {
    "type": "Polygon",
    "coordinates": [[[0.0, 0.0], [2.0, 0.0], [2.0, 2.0], [0.0, 2.0], [0.0, 0.0]]],
}
D1, D2 = datetime(2023, 1, 5), datetime(2023, 2, 1)


def _stack(*values: float) -> RasterStack:
    """One single-band 4x4 image for each value, on the grid (0, 0, 4, 4)."""
    return RasterStack.from_images(
        {
            date: ImageData(
                np.ma.array(np.full((1, 4, 4), value, np.float32)),
                band_descriptions=["b1"],
                crs=CRS.from_epsg(4326),
                bounds=(0, 0, 4, 4),
            )
            for date, value in zip((D1, D2), values, strict=False)
        }
    )


def _mean(data, **kwargs):
    return np.ma.mean(data)


class TestVectorCubeModel:
    """The VectorCube dataclass."""

    def test_geometry_only_cube(self):
        """A cube with geometries only has no `value` property."""
        cube = VectorCube.from_geojson(
            {"type": "Feature", "geometry": SQUARE, "properties": {"name": "a"}}
        )
        assert len(cube) == 1
        assert cube.dims == ("geometry",)
        assert cube.values is None
        feature = cube.to_feature_collection()["features"][0]
        assert feature["geometry"] is SQUARE
        assert feature["properties"] == {"name": "a"}

    @pytest.mark.parametrize(
        "obj, count",
        [
            (SQUARE, 1),
            ({"type": "Feature", "geometry": SQUARE, "properties": None}, 1),
            (
                {
                    "type": "FeatureCollection",
                    "features": [
                        {"type": "Feature", "geometry": SQUARE, "properties": {}}
                    ]
                    * 3,
                },
                3,
            ),
        ],
    )
    def test_from_geojson(self, obj, count):
        """A Geometry, a Feature and a FeatureCollection are accepted."""
        cube = VectorCube.from_geojson(obj)
        assert len(cube) == count
        assert cube.properties == [{}] * count

    def test_from_geojson_rejects_invalid(self):
        """A dict that is not GeoJSON is rejected."""
        with pytest.raises(ValueError, match="Invalid GeoJSON"):
            VectorCube.from_geojson({"foo": 1})

    def test_single_dimension_values(self):
        """With the geometry dimension only, the value goes in `value`."""
        cube = VectorCube(
            geometries=[SQUARE, SQUARE],
            values=np.ma.array([1.5, 2.0], mask=[False, True]),
        )
        features = cube.to_feature_collection()["features"]
        assert features[0]["properties"] == {"value": 1.5}
        assert features[1]["properties"]["value"] is np.ma.masked

    def test_nested_values(self):
        """The values are nested by dimension label, in dimension order."""
        values = np.ma.masked_all((1, 2, 2), dtype=object)
        values[0, 0, 0], values[0, 0, 1], values[0, 1, 0] = 1, 2, 3
        cube = VectorCube(
            geometries=[SQUARE],
            values=values,
            dims=("geometry", "t", "bands"),
            labels={"t": [D1, D2], "bands": ["B03", "B04"]},
        )
        nested = cube.to_feature_collection()["features"][0]["properties"]["values"]
        assert nested[D1] == {"B03": 1, "B04": 2}
        assert nested[D2]["B03"] == 3
        assert nested[D2]["B04"] is np.ma.masked

    def test_reprojects_to_epsg_4326(self):
        """GeoJSON output is in EPSG:4326 (RFC 7946); the cube keeps its CRS."""
        point = {"type": "Point", "coordinates": [500000.0, 0.0]}
        cube = VectorCube(geometries=[point], crs=CRS.from_epsg(32631))
        lon, lat = cube.to_feature_collection()["features"][0]["geometry"][
            "coordinates"
        ]
        assert lon == pytest.approx(3.0)
        assert lat == pytest.approx(0.0, abs=1e-9)
        assert cube.geometries[0] is point
        assert cube.__geo_interface__ == cube.to_feature_collection()

    @pytest.mark.parametrize(
        "kwargs, message",
        [
            ({"properties": [{}, {}]}, "one properties dict"),
            ({"dims": ("t",)}, "first dimension"),
            ({"dims": ("geometry", "t")}, "No labels"),
            (
                {"dims": ("geometry", "t"), "labels": {"t": [D1]}},
                "needs values",
            ),
            ({"values": np.ma.array([1.0, 2.0])}, "shape"),
        ],
    )
    def test_invalid_cube(self, kwargs: Dict[str, Any], message: str):
        """Inconsistent parts are rejected when the cube is made."""
        with pytest.raises(ValueError, match=message):
            VectorCube(geometries=[SQUARE], **kwargs)


class TestAggregateSpatialCube:
    """aggregate_spatial returns a VectorCube."""

    def test_dimensions_and_labels(self):
        """The cube has the dimensions geometry and t, and the target dimension."""
        cube = aggregate_spatial(
            data=_stack(1.0, 2.0),
            geometries=SQUARE,
            reducer=_mean,
            target_dimension="stats",
        )
        assert isinstance(cube, VectorCube)
        assert cube.dims == ("geometry", "t", "stats")
        assert cube.labels == {
            "t": [D1, D2],
            "stats": ["value", "total_count", "valid_count"],
        }
        assert cube.values.shape == (1, 2, 3)
        assert cube.crs == CRS.from_epsg(4326)

    def test_integer_result_stays_integer_in_json(self):
        """Each result keeps its type: a count is written as 4, not 4.0."""
        cube = aggregate_spatial(
            data=_stack(1.0),
            geometries=SQUARE,
            reducer=lambda data, **kwargs: int(np.ma.count(data)),
        )
        document = json.loads(encode(cube, "JSON").data)
        assert document["features"][0]["properties"]["values"] == {
            "2023-01-05T00:00:00": 4
        }

    def test_vector_cube_as_geometries(self):
        """The geometries of a vector cube can be aggregated again (zonal statistics)."""
        first = aggregate_spatial(data=_stack(1.0), geometries=SQUARE, reducer=_mean)
        again = aggregate_spatial(data=_stack(3.0), geometries=first, reducer=_mean)
        assert again.values[0, 0] == 3.0
        assert again.geometries == first.geometries

    def test_vector_data_kind_and_default_encoding(self):
        """A VectorCube is vector data, and JSON is its default encoding."""
        cube = aggregate_spatial(data=_stack(1.0), geometries=SQUARE, reducer=_mean)
        assert data_kind(cube) == "vector"
        result = encode_result(cube)
        assert result.media_type == "application/json"
        assert json.loads(result.data)["type"] == "FeatureCollection"


class TestVectorCubeThroughDecorator:
    """The @process decorator and vector cube arguments."""

    def test_dict_geometry_parameter_gets_geojson(self):
        """mask_polygon(mask: Dict) gets the FeatureCollection of a vector cube."""
        cube = VectorCube.from_geojson(SQUARE)
        result = process(mask_polygon)(
            positional_parameters={"data": 0, "mask": 1},
            named_parameters={"data": _stack(10.0), "mask": cube},
        )
        img = next(iter(result.values()))
        assert not img.array.mask[0, 3, 0]  # inside the square
        assert img.array.mask[0, 0, 3]  # outside the square

    def test_any_parameter_keeps_the_cube(self):
        """A parameter typed Any gets the VectorCube as it is."""

        def takes_any(data: Any) -> Any:
            return data

        cube = VectorCube.from_geojson(SQUARE)
        assert process(takes_any)(data=cube) is cube

    def test_raster_parameter_rejects_the_cube(self):
        """A raster-only parameter gives a clear error for a vector cube."""

        def takes_raster(data: RasterStack) -> RasterStack:
            return data

        with pytest.raises(ProcessParameterInvalid, match="'vector datacube'"):
            process(takes_raster)(data=VectorCube.from_geojson(SQUARE))
