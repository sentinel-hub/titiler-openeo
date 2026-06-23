"""The openEO aggregators reduce over the leading axis (axis 0), not globally.

In titiler's vectorized cube model the leading axis is the dimension an openEO
aggregator operates on: the time axis inside an ``apply_dimension(t)`` /
``array_apply`` callback, and the band axis inside ``reduce_dimension(bands)``.
Reducing globally (the old ``axis=None`` default on plain arrays) collapsed every
spatial pixel into a single scalar and broke per-pixel temporal compositing.

This mirrors the behaviour the RasterStack branch of each aggregator already has
(it reduces the stacked time axis) and the ``count``/``first``/``last`` plain-array
branches (which already index/reduce axis 0).
"""

import numpy as np
import pytest

from titiler.openeo.processes.implementations.math import max as max_
from titiler.openeo.processes.implementations.math import mean, median
from titiler.openeo.processes.implementations.math import min as min_
from titiler.openeo.processes.implementations.math import sd, stdev, variance

# A 2-element leading axis (e.g. 2 time slices or 2 bands) over a 1x3 strip, with
# spatially varying values so a per-axis reduction differs from a global one.
CUBE = np.array([[[1.0, 8.0, 3.0]], [[5.0, 2.0, 4.0]]])  # shape (2, 1, 3)


@pytest.mark.parametrize(
    "func, expected",
    [
        (max_, [[5.0, 8.0, 4.0]]),
        (min_, [[1.0, 2.0, 3.0]]),
        (mean, [[3.0, 5.0, 3.5]]),
        (median, [[3.0, 5.0, 3.5]]),
    ],
)
def test_aggregator_reduces_leading_axis(func, expected):
    """max/min/mean/median collapse axis 0, preserving spatial shape (1, 3)."""
    result = func(CUBE)
    assert np.asarray(result).shape == (1, 3)
    np.testing.assert_allclose(np.asarray(result), expected)


@pytest.mark.parametrize("func", [sd, stdev, variance])
def test_dispersion_aggregators_reduce_leading_axis(func):
    """sd/stdev/variance also reduce axis 0 (shape (1, 3)), not to a scalar."""
    result = func(CUBE)
    assert np.asarray(result).shape == (1, 3)


def test_aggregator_preserves_mask_over_leading_axis():
    """A pixel masked in every slice stays masked; otherwise the valid value wins."""
    arr = np.ma.MaskedArray(
        [[[1.0, 9.0]], [[5.0, 9.0]]],
        mask=[[[False, True]], [[False, True]]],  # col 1 masked in both slices
    )
    result = max_(arr)
    assert np.asarray(result).shape == (1, 2)
    assert bool(np.ma.getmaskarray(result)[0, 1]) is True
    assert float(result[0, 0]) == 5.0
