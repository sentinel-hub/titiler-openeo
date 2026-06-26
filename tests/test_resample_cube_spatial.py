"""Tests for the resample_cube_spatial process."""

from datetime import datetime

import numpy as np
import pytest
from openeo_pg_parser_networkx.graph import OpenEOProcessGraph
from rio_tiler.models import ImageData

from titiler.openeo.processes import process_registry
from titiler.openeo.processes.implementations.data_model import RasterStack
from titiler.openeo.processes.implementations.spatial import resample_cube_spatial


def _img(arr, bounds=(0, 0, 4, 4), crs="EPSG:4326", bands=None):
    return ImageData(
        np.ma.asanyarray(arr).astype("float32"),
        bounds=bounds,
        crs=crs,
        band_descriptions=bands,
    )


def _stack(img, key=datetime(2024, 1, 1)):
    return RasterStack.from_images({key: img})


def test_registered_in_process_registry():
    assert "resample_cube_spatial" in process_registry[None]


def test_downscale_same_crs_average():
    src = _stack(_img(np.arange(16).reshape(1, 4, 4), bands=["b"]))
    target = _stack(_img(np.zeros((1, 2, 2)), bounds=(0, 0, 4, 4)))

    out = resample_cube_spatial(src, target, method="average")
    img = out.first
    assert img.array.shape == (1, 2, 2)
    assert tuple(img.bounds) == (0, 0, 4, 4)
    assert img.band_descriptions == ["b"]  # bands preserved
    # block means of the 4x4 grid
    np.testing.assert_allclose(img.array[0], [[2.5, 4.5], [10.5, 12.5]])


def test_already_aligned_is_identity():
    src = _stack(_img(np.arange(16).reshape(1, 4, 4)))
    # target IS the source grid -> no work, same image returned
    out = resample_cube_spatial(src, src, method="near")
    assert out.first is src.first


def test_reproject_to_target_crs():
    src = _stack(_img(np.arange(16).reshape(1, 4, 4), crs="EPSG:4326"))
    target = _stack(
        _img(np.zeros((1, 8, 8)), bounds=(0, 0, 445277.96, 445640.1), crs="EPSG:3857")
    )
    out = resample_cube_spatial(src, target, method="near")
    assert out.first.array.shape == (1, 8, 8)
    from rasterio.crs import CRS as RioCRS

    assert RioCRS.from_user_input(out.first.crs).to_epsg() == 3857


def test_preserves_temporal_dimension():
    src = RasterStack.from_images(
        {
            datetime(2024, 1, 1): _img(np.ones((1, 4, 4))),
            datetime(2024, 1, 2): _img(np.full((1, 4, 4), 2.0)),
        }
    )
    target = _stack(_img(np.zeros((1, 2, 2))))
    out = resample_cube_spatial(src, target, method="near")
    assert sorted(out.keys()) == [datetime(2024, 1, 1), datetime(2024, 1, 2)]
    assert all(v.array.shape == (1, 2, 2) for v in out.values())


def test_nodata_mask_preserved():
    arr = np.ma.array(
        np.arange(16, dtype="float32").reshape(1, 4, 4),
        mask=np.zeros((1, 4, 4), dtype=bool),
    )
    arr.mask[0, :2, :2] = True  # mask the top-left quadrant
    src = _stack(_img(arr))
    target = _stack(_img(np.zeros((1, 2, 2))))
    out = resample_cube_spatial(src, target, method="near")
    # top-left output pixel comes from masked source -> stays masked
    assert bool(np.ma.getmaskarray(out.first.array)[0, 0, 0]) is True
    assert bool(np.ma.getmaskarray(out.first.array)[0, 1, 1]) is False


def test_unsupported_method():
    src = _stack(_img(np.zeros((1, 4, 4))))
    with pytest.raises(ValueError, match="Unsupported resampling method"):
        resample_cube_spatial(src, src, method="bogus")


def test_via_process_graph():
    src = _stack(_img(np.arange(16).reshape(1, 4, 4)))
    target = _stack(_img(np.zeros((1, 2, 2))))
    pg = {
        "process_graph": {
            "r": {
                "process_id": "resample_cube_spatial",
                "arguments": {
                    "data": {"from_parameter": "data"},
                    "target": {"from_parameter": "target"},
                    "method": "bilinear",
                },
                "result": True,
            }
        }
    }
    fn = OpenEOProcessGraph(pg_data=pg).to_callable(process_registry=process_registry)
    out = fn(named_parameters={"data": src, "target": target})
    assert out.first.array.shape == (1, 2, 2)
