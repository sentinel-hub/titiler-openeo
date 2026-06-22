"""Float-producing math ops compute integer inputs in float32, not float64."""

import numpy
import pytest

from titiler.openeo.processes.implementations.math import (
    divide,
    linear_scale_range,
    ln,
    normalized_difference,
    sqrt,
)


def test_normalized_difference_uint16_returns_float32():
    x = numpy.array([10000, 4000], dtype="uint16")
    y = numpy.array([2000, 8000], dtype="uint16")
    out = normalized_difference(x, y)
    assert out.dtype == numpy.float32


def test_normalized_difference_no_integer_wraparound():
    """y > x must give a negative result, not a wrapped huge positive one.

    In pure uint16, ``10000 - 20000`` wraps to 55536 and the ratio comes out
    positive (~1.85) instead of -1/3.
    """
    x = numpy.array([10000], dtype="uint16")
    y = numpy.array([20000], dtype="uint16")
    assert normalized_difference(x, y)[0] == pytest.approx(-1.0 / 3.0, rel=1e-5)


def test_normalized_difference_no_addition_overflow():
    x = numpy.array([60000], dtype="uint16")
    y = numpy.array([40000], dtype="uint16")
    # (60000 - 40000) / (60000 + 40000) = 0.2 — would overflow uint16 in (x + y)
    assert normalized_difference(x, y)[0] == pytest.approx(0.2, rel=1e-5)


def test_divide_integer_inputs_return_float32():
    out = divide(
        numpy.array([6, 9], dtype="uint16"), numpy.array([2, 3], dtype="int32")
    )
    assert out.dtype == numpy.float32
    assert numpy.allclose(out, [3.0, 3.0])


def test_linear_scale_range_integer_input_returns_float32():
    out = linear_scale_range(numpy.array([0, 5, 10], dtype="uint16"), 0, 10)
    assert out.dtype == numpy.float32
    assert numpy.allclose(out, [0.0, 0.5, 1.0])


def test_transcendental_integer_input_returns_float32():
    assert sqrt(numpy.array([4, 9], dtype="uint16")).dtype == numpy.float32
    assert ln(numpy.array([1, 2], dtype="int32")).dtype == numpy.float32


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
    assert normalized_difference(x, y).dtype == numpy.float32


def test_mask_is_preserved():
    x = numpy.ma.MaskedArray([10000, 4000], mask=[True, False], dtype="uint16")
    y = numpy.ma.MaskedArray([2000, 8000], mask=[False, False], dtype="uint16")
    out = normalized_difference(x, y)
    assert isinstance(out, numpy.ma.MaskedArray)
    assert bool(out.mask[0]) is True
    assert out.dtype == numpy.float32


def test_python_scalars_still_work():
    assert normalized_difference(3, 1) == pytest.approx(0.5)
    assert divide(6, 2) == pytest.approx(3.0)
