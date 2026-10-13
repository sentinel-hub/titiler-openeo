"""Tests for the slope, aspect and apply_kernel processes (#455)."""

from datetime import datetime

import numpy as np
import pytest
from openeo_pg_parser_networkx.graph import OpenEOProcessGraph
from rasterio.crs import CRS
from rio_tiler.models import ImageData

from titiler.openeo.processes import process_registry
from titiler.openeo.processes.implementations.data_model import RasterStack
from titiler.openeo.processes.implementations.dem import aspect, slope
from titiler.openeo.processes.implementations.focal import (
    KernelDimensionsUneven,
    apply_kernel,
)

UTM = CRS.from_epsg(32631)


def _plane_stack(dz_dx: float, dz_dy: float, res: float = 20.0) -> RasterStack:
    """A 1-band DEM plane z = dz_dx * east + dz_dy * north on a UTM grid."""
    size = 6
    east = (np.arange(size) + 0.5) * res
    north = (size - np.arange(size) - 0.5) * res
    z = dz_dx * east[np.newaxis, :] + dz_dy * north[:, np.newaxis]
    img = ImageData(
        np.ma.MaskedArray(z[np.newaxis].astype("float32")),
        crs=UTM,
        bounds=(0, 0, size * res, size * res),
        band_descriptions=["dem"],
    )
    return RasterStack.from_images({datetime(2024, 1, 1): img})


def _first(stack: RasterStack) -> ImageData:
    return next(iter(stack.values()))


def test_slope_uses_pixel_size_in_metres():
    """A 10% grade gives atan(0.1) on a 20 m grid, not 20 times steeper."""
    result = _first(slope(_plane_stack(0.1, 0.0)))
    inner = result.array[0, 1:-1, 1:-1]
    np.testing.assert_allclose(inner, np.arctan(0.1), rtol=1e-5)
    assert result.band_descriptions == ["dem_slope"]
    assert result.array.dtype == np.float32


def test_slope_keeps_grid_and_masks_border():
    """The output has the input grid; the 1-pixel border is no-data."""
    stack = _plane_stack(0.1, 0.2)
    result = _first(slope(stack))
    assert result.array.shape == (1, 6, 6)
    assert result.bounds == _first(stack).bounds
    mask = np.ma.getmaskarray(result.array)[0]
    assert mask[0].all() and mask[-1].all()
    assert mask[:, 0].all() and mask[:, -1].all()
    assert not mask[1:-1, 1:-1].any()


@pytest.mark.parametrize(
    "dz_dx,dz_dy,expected",
    [
        (0.0, -0.1, 0.0),  # lower to the north: faces north
        (-0.1, 0.0, np.pi / 2),  # faces east
        (0.0, 0.1, np.pi),  # faces south
        (0.1, 0.0, 3 * np.pi / 2),  # faces west
    ],
)
def test_aspect_downslope_clockwise_from_north(dz_dx, dz_dy, expected):
    """The aspect is the downslope direction, clockwise from north (GDAL)."""
    result = _first(aspect(_plane_stack(dz_dx, dz_dy)))
    np.testing.assert_allclose(result.array[0, 1:-1, 1:-1], expected, atol=1e-6)
    assert result.band_descriptions == ["dem_aspect"]


def test_aspect_is_nan_on_flat_terrain():
    """There is no gradient direction on a flat surface."""
    result = _first(aspect(_plane_stack(0.0, 0.0)))
    assert np.isnan(result.array.data[0, 1:-1, 1:-1]).all()


def test_slope_geographic_crs_converts_degrees_to_metres():
    """On EPSG:4326 the pixel size is converted to metres."""
    size = 5
    res_deg = 0.001
    metres = np.pi / 180 * 6371008.8 * res_deg
    north = (size - np.arange(size) - 0.5)[:, np.newaxis] * metres
    z = np.broadcast_to(0.1 * north, (size, size))
    img = ImageData(
        np.ma.MaskedArray(z[np.newaxis].astype("float64")),
        crs=CRS.from_epsg(4326),
        bounds=(0, 0, size * res_deg, size * res_deg),
    )
    result = _first(slope(RasterStack.from_images({datetime(2024, 1, 1): img})))
    np.testing.assert_allclose(result.array[0, 1:-1, 1:-1], np.arctan(0.1), rtol=1e-4)


def test_slope_masks_neighbours_of_nodata():
    """A no-data pixel makes its neighbours no-data."""
    stack = _plane_stack(0.1, 0.0)
    img = _first(stack)
    img.array.mask = np.zeros(img.array.shape, dtype=bool)
    img.array.mask[0, 3, 3] = True
    mask = np.ma.getmaskarray(_first(slope(stack)).array)[0]
    assert mask[3, 2] and mask[2, 3] and mask[3, 4] and mask[4, 3]
    assert not mask[1, 1]


def _kernel_stack(values: np.ndarray) -> RasterStack:
    img = ImageData(np.ma.MaskedArray(values[np.newaxis].astype("float32")))
    return RasterStack.from_images({datetime(2024, 1, 1): img})


def test_apply_kernel_box_mean():
    """A 3x3 mean kernel with factor 1/9 averages the neighbourhood."""
    data = np.arange(25, dtype="float32").reshape(5, 5)
    result = _first(apply_kernel(_kernel_stack(data), np.ones((3, 3)).tolist(), 1 / 9))
    assert result.array[0, 2, 2] == pytest.approx(12.0)
    # Default border fills with zero.
    assert result.array[0, 0, 0] == pytest.approx((0 + 1 + 5 + 6) / 9)


def test_apply_kernel_is_a_true_convolution():
    """The kernel is flipped, as in scipy.ndimage.convolve."""
    data = np.zeros((3, 3), dtype="float32")
    data[1, 1] = 1
    kernel = [[1, 2, 3], [4, 5, 6], [7, 8, 9]]
    result = _first(apply_kernel(_kernel_stack(data), kernel))
    np.testing.assert_array_equal(result.array[0], kernel)


@pytest.mark.parametrize(
    "border,expected",
    [
        ("replicate", 1 + 1 + 2),
        ("reflect", 1 + 1 + 2),
        ("reflect_pixel", 2 + 1 + 2),
        ("wrap", 3 + 1 + 2),
        (10, 10 + 1 + 2),
    ],
)
def test_apply_kernel_border_modes(border, expected):
    """Each border mode extends the data as the openEO spec shows."""
    data = np.array([[1, 2, 3]], dtype="float32")
    result = _first(apply_kernel(_kernel_stack(data), [[1, 1, 1]], border=border))
    assert result.array[0, 0, 0] == pytest.approx(expected)


def test_apply_kernel_replaces_invalid_and_keeps_nodata():
    """NaN and no-data are replaced for the sum; no-data stays no-data."""
    data = np.ma.MaskedArray(
        [[[1.0, np.nan, 1.0]]], mask=[[[False, False, True]]], dtype="float32"
    )
    stack = RasterStack.from_images({datetime(2024, 1, 1): ImageData(data)})
    result = _first(apply_kernel(stack, [[1, 1, 1]], replace_invalid=5))
    assert result.array.data[0, 0, 0] == pytest.approx(0 + 1 + 5)
    assert np.ma.getmaskarray(result.array)[0, 0].tolist() == [False, False, True]


def test_apply_kernel_even_kernel_raises():
    with pytest.raises(KernelDimensionsUneven):
        apply_kernel(_kernel_stack(np.zeros((3, 3))), [[1, 1], [1, 1]])


def test_terrain_processes_via_graph():
    """slope, aspect and apply_kernel run through the real executor."""
    pg = {
        "slope": {
            "process_id": "slope",
            "arguments": {"data": {"from_parameter": "data"}},
        },
        "kernel": {
            "process_id": "apply_kernel",
            "arguments": {
                "data": {"from_node": "slope"},
                "kernel": [[0, 0, 0], [0, 1, 0], [0, 0, 0]],
            },
            "result": True,
        },
    }
    callable_ = OpenEOProcessGraph(pg_data={"process_graph": pg}).to_callable(
        process_registry=process_registry
    )
    result = callable_(named_parameters={"data": _plane_stack(0.1, 0.0)})
    img = _first(result)
    np.testing.assert_allclose(img.array[0, 1:-1, 1:-1], np.arctan(0.1), rtol=1e-5)
    assert img.band_descriptions == ["dem_slope"]


def test_slope_is_lazy_on_a_task_stack():
    """On a stack with read tasks, nothing is read until a slice is used."""
    calls = []
    img = _first(_plane_stack(0.1, 0.0))

    def task():
        calls.append(1)
        return img

    stack = RasterStack(
        tasks=[(task, {"datetime": datetime(2024, 1, 1)})],
        timestamp_fn=lambda asset: asset["datetime"],
        width=img.width,
        height=img.height,
        bounds=img.bounds,
        dst_crs=img.crs,
        band_names=["dem"],
    )
    result = slope(stack)
    assert calls == []
    assert result.band_names == ["dem_slope"]
    np.testing.assert_allclose(
        _first(result).array[0, 1:-1, 1:-1], np.arctan(0.1), rtol=1e-5
    )
    assert calls == [1]
