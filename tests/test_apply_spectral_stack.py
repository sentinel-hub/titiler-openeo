"""Unit tests for _apply_spectral_dimension_stack result-shape handling.

These call the helper directly with a crafted ``process`` so every branch of the
single-call result reshaping (4D -> per-time, 3D -> single band, and the error
guards) is exercised. The end-to-end "evaluate once" behaviour is covered by the
process-graph tests in test_apply_graph_integration.py.
"""

from datetime import datetime

import numpy as np
import pytest
from rio_tiler.models import ImageData

from titiler.openeo.processes.implementations.apply import (
    _apply_spectral_dimension_stack,
)
from titiler.openeo.processes.implementations.data_model import RasterStack

KEYS = [datetime(2024, 6, 1), datetime(2024, 6, 2)]


def _stack():
    """2 timestamps, 2 bands, 1x2 spatial, distinct per-slice values."""
    return RasterStack.from_images(
        {
            KEYS[0]: ImageData(
                np.ma.array(np.stack([np.full((1, 2), 1.0), np.full((1, 2), 2.0)])),
                band_descriptions=["b0", "b1"],
            ),
            KEYS[1]: ImageData(
                np.ma.array(np.stack([np.full((1, 2), 10.0), np.full((1, 2), 20.0)])),
                band_descriptions=["b0", "b1"],
            ),
        }
    )


def _run(proc):
    return _apply_spectral_dimension_stack(
        _stack(), proc, positional_parameters={"data": 0}, named_parameters={}
    )


def test_4d_result_splits_into_per_time_bands():
    """A (out_bands, time, h, w) result maps back to per-time (out_bands, h, w)."""
    # process receives (bands, time, h, w); return 2 output bands unchanged.
    res = _run(lambda arr, **kw: arr)
    assert isinstance(res, RasterStack)
    assert res[KEYS[0]].array.shape == (2, 1, 2)
    np.testing.assert_array_equal(res[KEYS[0]].array[:, 0, 0], [1.0, 2.0])
    np.testing.assert_array_equal(res[KEYS[1]].array[:, 0, 0], [10.0, 20.0])


def test_3d_result_becomes_single_band_per_time():
    """A (time, h, w) result (spectral dim reduced to one band) -> (1, h, w)."""
    # Reduce bands by summing axis 0 -> (time, h, w).
    res = _run(lambda arr, **kw: arr.sum(axis=0))
    assert res[KEYS[0]].array.shape == (1, 1, 2)
    assert float(res[KEYS[0]].array[0, 0, 0]) == 3.0  # 1 + 2
    assert float(res[KEYS[1]].array[0, 0, 0]) == 30.0  # 10 + 20


def test_non_array_result_raises():
    with pytest.raises(ValueError, match="must return a numpy array"):
        _run(lambda arr, **kw: "not an array")


def test_unexpected_ndim_raises():
    # 2D result (h, w) is ambiguous for a multi-temporal stack.
    with pytest.raises(ValueError, match="3D .* or 4D"):
        _run(lambda arr, **kw: np.zeros((1, 2)))


def test_changed_temporal_size_raises():
    # Drop a timestamp -> result no longer matches the stack's time dimension.
    with pytest.raises(ValueError, match="temporal dimension size"):
        _run(lambda arr, **kw: arr[:, :1])


def test_empty_stack_raises():
    with pytest.raises(ValueError, match="non-empty RasterStack"):
        _apply_spectral_dimension_stack(
            {}, lambda arr, **kw: arr, positional_parameters={}, named_parameters={}
        )
