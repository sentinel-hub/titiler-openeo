"""Tests for normalized_difference dtype/precision (EPIC #305, subtask 2)."""

from datetime import datetime

import numpy
import pytest
from rio_tiler.models import ImageData

from titiler.openeo.processes.implementations.data_model import RasterStack
from titiler.openeo.processes.implementations.indices import ndvi
from titiler.openeo.processes.implementations.math import normalized_difference


def test_uint16_inputs_return_float32():
    """The common reflectance path (uint16) must not blow up to float64."""
    x = numpy.array([10000, 4000], dtype="uint16")
    y = numpy.array([2000, 8000], dtype="uint16")
    out = normalized_difference(x, y)
    assert out.dtype == numpy.float32


def test_no_integer_wraparound():
    """y > x must give a negative result, not a wrapped huge positive one.

    In pure uint16, ``10000 - 20000`` wraps to 55536 and ``(x - y)/(x + y)``
    comes out positive (~1.85) instead of -1/3.
    """
    x = numpy.array([10000], dtype="uint16")
    y = numpy.array([20000], dtype="uint16")
    out = normalized_difference(x, y)
    assert out[0] == pytest.approx(-1.0 / 3.0, rel=1e-5)


def test_no_addition_overflow():
    """x + y exceeding the uint16 max (65535) must not overflow."""
    x = numpy.array([60000], dtype="uint16")
    y = numpy.array([40000], dtype="uint16")
    out = normalized_difference(x, y)
    # (60000 - 40000) / (60000 + 40000) = 0.2
    assert out[0] == pytest.approx(0.2, rel=1e-5)


def test_float64_inputs_are_not_downcast():
    """Genuine float64 inputs keep their precision (we only promote integers)."""
    x = numpy.array([0.6], dtype="float64")
    y = numpy.array([0.2], dtype="float64")
    out = normalized_difference(x, y)
    assert out.dtype == numpy.float64
    assert out[0] == pytest.approx(0.5)


def test_float32_inputs_stay_float32():
    x = numpy.array([0.6], dtype="float32")
    y = numpy.array([0.2], dtype="float32")
    out = normalized_difference(x, y)
    assert out.dtype == numpy.float32


def test_mask_is_preserved():
    """Promotion via astype must not drop the mask."""
    x = numpy.ma.MaskedArray([10000, 4000], mask=[True, False], dtype="uint16")
    y = numpy.ma.MaskedArray([2000, 8000], mask=[False, False], dtype="uint16")
    out = normalized_difference(x, y)
    assert isinstance(out, numpy.ma.MaskedArray)
    assert bool(out.mask[0]) is True
    assert bool(out.mask[1]) is False
    assert out.dtype == numpy.float32


def test_python_scalars_still_work():
    """Non-array inputs (no .dtype) fall through unchanged."""
    assert normalized_difference(3, 1) == pytest.approx(0.5)


def test_ndvi_end_to_end_is_float32():
    """The full ndvi() path on a uint16 stack yields a float32 index cube."""
    arr = numpy.ma.MaskedArray(
        numpy.random.randint(1, 10000, (2, 8, 8)).astype("uint16"),
        mask=numpy.zeros((2, 8, 8), dtype=bool),
    )
    stack = RasterStack.from_images({datetime(2020, 1, 1): ImageData(arr)})
    result = ndvi(stack, nir=2, red=1)
    img = result.first
    assert img.array.dtype == numpy.float32
