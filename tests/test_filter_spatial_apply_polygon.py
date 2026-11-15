"""filter_spatial and apply_polygon (ADR 0014 §7.4)."""

from datetime import datetime

import numpy as np
import pytest
from openeo_pg_parser_networkx import OpenEOProcessGraph
from rasterio.crs import CRS
from rasterio.warp import transform_geom
from rio_tiler.models import ImageData

from titiler.openeo.errors import (
    GeometriesOverlap,
    OpenEOException,
    ProcessParameterInvalid,
)
from titiler.openeo.processes import process_registry
from titiler.openeo.processes.implementations.data_model import RasterStack
from titiler.openeo.processes.implementations.vector_model import VectorCube

UTM = CRS.from_epsg(32631)
D1, D2 = datetime(2023, 1, 5), datetime(2023, 2, 1)
# A 4 x 4 grid of 10 m pixels. Row 0 is the north row.
BOUNDS = (500000.0, 5540000.0, 500040.0, 5540040.0)
PROCESSES = process_registry[None]
filter_spatial = PROCESSES["filter_spatial"].implementation
apply_polygon = PROCESSES["apply_polygon"].implementation


def _stack(dates=(D1,), mask=None) -> RasterStack:
    """One band: the value of a pixel is row * 4 + column (+ 100 per date)."""
    images = {}
    for d, date in enumerate(dates):
        array = np.arange(16, dtype="float64").reshape(1, 4, 4) + 100 * d
        images[date] = ImageData(
            np.ma.masked_array(array, mask=mask if mask is not None else False),
            crs=UTM,
            bounds=BOUNDS,
            band_descriptions=["B04"],
        )
    return RasterStack.from_images(images)


def _box(col0, row0, col1, row1) -> dict:
    """A polygon on whole pixels: columns col0..col1-1, rows row0..row1-1."""
    x0, x1 = BOUNDS[0] + 10 * col0, BOUNDS[0] + 10 * col1
    y0, y1 = BOUNDS[3] - 10 * row1, BOUNDS[3] - 10 * row0
    return {
        "type": "Polygon",
        "coordinates": [[[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]],
    }


def _cube(*geometries) -> VectorCube:
    return VectorCube(geometries=list(geometries), crs=UTM)


def _times(factor):
    """A Python callback: the sub data cube multiplied by `factor`."""

    def process(data, positional_parameters=None, named_parameters=None):
        return data.map_images(
            lambda img: ImageData(
                img.array * factor,
                crs=img.crs,
                bounds=img.bounds,
                band_descriptions=img.band_descriptions,
            )
        )

    return process


# filter_spatial


def test_filter_spatial_crops_and_masks():
    """Crop to the window of the kept pixels; the others have no data."""
    triangle = {
        "type": "Polygon",
        # Pixel centers (1, 1), (2, 1) and (1, 2) are inside; (2, 2) is not.
        "coordinates": [
            [
                [BOUNDS[0] + 10, BOUNDS[3] - 10],
                [BOUNDS[0] + 30, BOUNDS[3] - 10],
                [BOUNDS[0] + 10, BOUNDS[3] - 30],
                [BOUNDS[0] + 10, BOUNDS[3] - 10],
            ]
        ],
    }
    result = filter_spatial(data=_stack(), geometries=_cube(triangle))

    image = result[D1]
    assert (image.width, image.height) == (2, 2)
    assert tuple(image.bounds) == (
        BOUNDS[0] + 10,
        BOUNDS[3] - 30,
        BOUNDS[0] + 30,
        BOUNDS[3] - 10,
    )
    assert image.array[0].tolist() == [[5.0, 6.0], [9.0, None]]
    assert (result.width, result.height) == (2, 2)
    assert tuple(result.bounds) == tuple(image.bounds)


def test_filter_spatial_points_and_lines():
    """A point keeps its pixel, a line the pixels that it crosses."""
    point = {"type": "Point", "coordinates": [BOUNDS[0] + 35, BOUNDS[3] - 5]}
    line = {
        "type": "LineString",
        "coordinates": [
            [BOUNDS[0] + 5, BOUNDS[3] - 25],
            [BOUNDS[0] + 35, BOUNDS[3] - 25],
        ],
    }
    image = filter_spatial(data=_stack(), geometries=_cube(point, line))[D1]
    assert image.array[0].tolist() == [
        [None, None, None, 3.0],
        [None, None, None, None],
        [8.0, 9.0, 10.0, 11.0],
    ]


def test_filter_spatial_geojson_in_epsg4326():
    """GeoJSON (deprecated) is in EPSG:4326 and reprojected to the cube CRS."""
    geometry = transform_geom(UTM, "EPSG:4326", _box(2, 2, 4, 4))
    image = filter_spatial(data=_stack(), geometries=geometry)[D1]
    assert image.array[0].tolist() == [[10.0, 11.0], [14.0, 15.0]]


def test_filter_spatial_keeps_no_data():
    """A no-data pixel stays no-data."""
    mask = np.zeros((1, 4, 4), dtype=bool)
    mask[0, 0, 0] = True
    image = filter_spatial(data=_stack(mask=mask), geometries=_cube(_box(0, 0, 2, 1)))[
        D1
    ]
    assert image.array[0].tolist() == [[None, 1.0]]


def test_filter_spatial_no_pixel():
    """Geometries that cover no pixel: NoDataAvailable."""
    far = {"type": "Point", "coordinates": [0.0, 0.0]}
    with pytest.raises(OpenEOException) as error:
        filter_spatial(data=_stack(), geometries=_cube(far))
    assert error.value.code == "NoDataAvailable"
    assert error.value.status_code == 400


def test_filter_spatial_graph_to_gtiff():
    """load_geojson -> filter_spatial -> save_result GTiff."""
    import rasterio

    graph = {
        "area": {
            "process_id": "load_geojson",
            "arguments": {"data": transform_geom(UTM, "EPSG:4326", _box(0, 0, 2, 2))},
        },
        "filter": {
            "process_id": "filter_spatial",
            "arguments": {
                "data": {"from_parameter": "data"},
                "geometries": {"from_node": "area"},
            },
        },
        "save": {
            "process_id": "save_result",
            "arguments": {"data": {"from_node": "filter"}, "format": "GTiff"},
            "result": True,
        },
    }
    run = OpenEOProcessGraph(pg_data={"process_graph": graph}).to_callable(
        process_registry=process_registry
    )
    data = run(named_parameters={"data": _stack()}).data
    with rasterio.MemoryFile(data) as memfile, memfile.open() as dataset:
        assert (dataset.width, dataset.height) == (2, 2)
        assert dataset.read(1).tolist() == [[0.0, 1.0], [4.0, 5.0]]


# apply_polygon


def test_apply_polygon_sub_cubes():
    """One call for each polygon, with a sub data cube cropped to it."""
    calls = []

    def process(data, positional_parameters=None, named_parameters=None):
        calls.append((data.width, data.height, data.first.array.filled(-9)[0].tolist()))
        assert named_parameters["context"] == {"k": 1}
        return data

    triangle = {
        "type": "Polygon",
        "coordinates": [
            [
                [BOUNDS[0] + 10, BOUNDS[3] - 10],
                [BOUNDS[0] + 30, BOUNDS[3] - 10],
                [BOUNDS[0] + 10, BOUNDS[3] - 30],
                [BOUNDS[0] + 10, BOUNDS[3] - 10],
            ]
        ],
    }
    apply_polygon(
        data=_stack(),
        geometries=_cube(_box(3, 3, 4, 4), triangle),
        process=process,
        context={"k": 1},
    )
    # The pixel (2, 2) is outside the triangle: no data (mask_value null).
    assert calls == [(1, 1, [[15.0]]), (2, 2, [[5.0, 6.0], [9.0, -9.0]])]


@pytest.mark.parametrize("mask_value,outside", [(None, None), (-1, -1.0)])
def test_apply_polygon_writes_back(mask_value, outside):
    """Polygon pixels: the result. Other pixels: mask_value (null: no data)."""
    result = apply_polygon(
        data=_stack(dates=(D1, D2)),
        geometries=_cube(_box(0, 0, 2, 2), _box(2, 2, 4, 4)),
        process=_times(10),
        mask_value=mask_value,
    )
    assert list(result.keys()) == [D1, D2]
    o = outside
    assert result[D1].array[0].tolist() == [
        [0.0, 10.0, o, o],
        [40.0, 50.0, o, o],
        [o, o, 100.0, 110.0],
        [o, o, 140.0, 150.0],
    ]
    assert result[D2].array[0, 0, 0] == 1000.0
    assert result[D1].band_descriptions == ["B04"]


def test_apply_polygon_mask_value_is_not_no_data():
    """mask_value marks the pixels outside the polygons, not the no-data ones."""
    mask = np.zeros((1, 4, 4), dtype=bool)
    mask[0, 0, 0] = True
    result = apply_polygon(
        data=_stack(mask=mask),
        geometries=_cube(_box(0, 0, 1, 2)),
        process=_times(1),
        mask_value=0,
    )
    assert result[D1].array[0, :2, :2].tolist() == [[None, 0.0], [4.0, 0.0]]


def test_apply_polygon_graph_callback_per_polygon():
    """A process graph callback runs for each polygon, on its own pixels."""
    callback = {
        "process_graph": {
            "apply": {
                "process_id": "apply",
                "arguments": {
                    "data": {"from_parameter": "data"},
                    "process": {
                        "process_graph": {
                            "times": {
                                "process_id": "multiply",
                                "arguments": {"x": {"from_parameter": "x"}, "y": 2},
                                "result": True,
                            }
                        }
                    },
                },
                "result": True,
            }
        }
    }
    graph = {
        "zones": {
            "process_id": "load_geojson",
            "arguments": {
                "data": {
                    "type": "FeatureCollection",
                    "features": [
                        {
                            "type": "Feature",
                            "geometry": transform_geom(UTM, "EPSG:4326", box),
                            "properties": {},
                        }
                        for box in (_box(0, 0, 1, 1), _box(1, 1, 3, 3))
                    ],
                }
            },
        },
        "segments": {
            "process_id": "apply_polygon",
            "arguments": {
                "data": {"from_parameter": "data"},
                "geometries": {"from_node": "zones"},
                "process": callback,
            },
            "result": True,
        },
    }
    run = OpenEOProcessGraph(pg_data={"process_graph": graph}).to_callable(
        process_registry=process_registry
    )
    image = run(named_parameters={"data": _stack()})[D1]
    assert image.array[0].tolist() == [
        [0.0, None, None, None],
        [None, 10.0, 12.0, None],
        [None, 18.0, 20.0, None],
        [None, None, None, None],
    ]


def test_apply_polygon_errors():
    """Overlap, a geometry that is not a polygon, a string mask_value, a bad result."""
    with pytest.raises(GeometriesOverlap) as error:
        apply_polygon(
            data=_stack(),
            geometries=_cube(_box(0, 0, 2, 2), _box(1, 1, 3, 3)),
            process=_times(1),
        )
    assert error.value.code == "GeometriesOverlap"

    point = {"type": "Point", "coordinates": [BOUNDS[0] + 5, BOUNDS[3] - 5]}
    with pytest.raises(ProcessParameterInvalid, match=r"polygons, got \['Point'\]"):
        apply_polygon(data=_stack(), geometries=_cube(point), process=_times(1))

    with pytest.raises(ProcessParameterInvalid, match="not a string"):
        apply_polygon(
            data=_stack(),
            geometries=_cube(_box(0, 0, 1, 1)),
            process=_times(1),
            mask_value="outside",
        )

    def shrink(data, positional_parameters=None, named_parameters=None):
        return data.map_images(
            lambda img: ImageData(img.array[:, :1, :1], crs=img.crs, bounds=img.bounds)
        )

    with pytest.raises(ProcessParameterInvalid, match="same bands and pixels"):
        apply_polygon(data=_stack(), geometries=_cube(_box(0, 0, 2, 2)), process=shrink)


def test_apply_polygon_polygon_without_pixel():
    """A polygon that contains no pixel center is not processed."""
    sliver = {
        "type": "Polygon",
        "coordinates": [
            [
                [BOUNDS[0] + 1, BOUNDS[3] - 1],
                [BOUNDS[0] + 2, BOUNDS[3] - 1],
                [BOUNDS[0] + 2, BOUNDS[3] - 2],
                [BOUNDS[0] + 1, BOUNDS[3] - 1],
            ]
        ],
    }
    calls = []

    def process(data, positional_parameters=None, named_parameters=None):
        calls.append(1)
        return data

    result = apply_polygon(
        data=_stack(), geometries=_cube(sliver), process=process, mask_value=-1
    )
    assert calls == []
    assert (result[D1].array == -1).all()
