"""Tests that nodata masks survive the stacking operations in the process graph.

Regression coverage for the "satellite swath edge" artifact: pixels flagged as
nodata at load time (e.g. Sentinel-2 nodata=0 at acquisition edges) must stay
masked through every operation that recombines per-slice or per-band arrays.

The root cause was the use of ``numpy.stack`` on lists of ``MaskedArray``, which
silently drops the masks (returning an all-``False`` mask) instead of combining
them. Every such site must use ``numpy.ma.stack``. These tests assert the masks
are preserved end-to-end so the bug cannot regress.
"""

from datetime import datetime

import numpy as np
import pytest
from rio_tiler.models import ImageData

from titiler.openeo.processes.implementations.arrays import array_create, array_element
from titiler.openeo.processes.implementations.data_model import RasterStack
from titiler.openeo.processes.implementations.math import max as max_
from titiler.openeo.processes.implementations.math import min as min_
from titiler.openeo.processes.implementations.reduce import reduce_dimension


def _masked_image(values, mask):
    """Build a single-band ImageData from a 2D values/mask pair."""
    arr = np.ma.MaskedArray(
        np.asarray(values, dtype="float64")[np.newaxis, ...],
        mask=np.asarray(mask, dtype=bool)[np.newaxis, ...],
    )
    return ImageData(arr, band_descriptions=["b"])


def test_numpy_stack_drops_masks_baseline():
    """Document the trap that motivates these tests: numpy.stack loses masks."""
    a = np.ma.MaskedArray([1.0, 2.0], mask=[True, False])
    b = np.ma.MaskedArray([3.0, 4.0], mask=[False, True])

    # Plain numpy.stack silently drops the masks ...
    assert not np.ma.getmaskarray(np.stack([a, b], axis=0)).any()
    # ... while numpy.ma.stack preserves them.
    assert np.ma.getmaskarray(np.ma.stack([a, b], axis=0)).tolist() == [
        [True, False],
        [False, True],
    ]


def test_array_create_preserves_masks():
    """array_create stacks band callbacks; masked nodata must survive."""
    band0 = np.ma.MaskedArray([[1.0, 2.0]], mask=[[True, False]])
    band1 = np.ma.MaskedArray([[3.0, 4.0]], mask=[[False, False]])

    result = array_create(data=[band0, band1])

    assert isinstance(result, np.ma.MaskedArray)
    assert np.ma.getmaskarray(result).tolist() == [[[True, False]], [[False, False]]]


def test_array_element_preserves_masks_over_stack():
    """array_element over a RasterStack must keep each slice's nodata mask."""
    stack = RasterStack.from_images(
        {
            datetime(2024, 6, 1): _masked_image([[1.0, 2.0]], [[True, False]]),
            datetime(2024, 6, 2): _masked_image([[3.0, 4.0]], [[False, True]]),
        }
    )

    result = array_element(stack, 0)

    assert isinstance(result, np.ma.MaskedArray)
    # Shape is (time, height, width) after taking band 0 from each slice.
    assert np.ma.getmaskarray(result).tolist() == [[[True, False]], [[False, True]]]


def test_array_element_preserves_mask_on_band_index():
    """array_element selecting a band from a MaskedArray (the common callback case,
    e.g. extracting SCL or a reflectance band inside a reducer) must keep the mask."""
    arr = np.ma.MaskedArray(
        np.array([[[1.0, 2.0]], [[3.0, 4.0]]]),
        mask=np.array([[[True, False]], [[False, True]]]),
    )

    result = array_element(arr, index=0)

    assert isinstance(result, np.ma.MaskedArray)
    assert np.ma.getmaskarray(result).tolist() == [[True, False]]


@pytest.mark.parametrize("reducer", [max_, min_])
def test_max_min_over_rasterstack_preserve_masks(reducer):
    """Temporal max/min must mask pixels that are nodata in every slice and keep
    valid values where at least one slice has data."""
    # Pixel (0,0): masked in both slices -> stays masked.
    # Pixel (0,1): valid in both -> reducer picks across the two values.
    stack = RasterStack.from_images(
        {
            datetime(2024, 6, 1): _masked_image([[10.0, 2.0]], [[True, False]]),
            datetime(2024, 6, 2): _masked_image([[20.0, 5.0]], [[True, False]]),
        }
    )

    result = reducer(stack)

    assert isinstance(result, np.ma.MaskedArray)
    mask = np.ma.getmaskarray(result)
    assert bool(mask[..., 0]) is True
    assert bool(mask[..., 1]) is False


def test_max_over_rasterstack_keeps_value_when_one_slice_valid():
    """A pixel that is nodata in one slice but valid in another must not be
    contaminated by the masked underlying value."""
    # Underlying masked value (999.0) is larger than the valid value; if the mask
    # were dropped, max would wrongly return 999.0.
    stack = RasterStack.from_images(
        {
            datetime(2024, 6, 1): _masked_image([[999.0]], [[True]]),
            datetime(2024, 6, 2): _masked_image([[7.0]], [[False]]),
        }
    )

    result = max_(stack)

    assert not bool(np.ma.getmaskarray(result)[..., 0])
    assert float(result[..., 0]) == 7.0


def test_spectral_reduce_preserves_mask_through_stack():
    """reduce_dimension over bands stacks per-image arrays; nodata must persist."""

    def reducer(data):
        # data shape: (bands, time, height, width) -> reduce over bands (axis 0).
        return np.ma.max(data, axis=0)

    stack = RasterStack.from_images(
        {
            datetime(2024, 6, 1): ImageData(
                np.ma.MaskedArray(
                    np.array([[[1.0, 2.0]], [[3.0, 4.0]]]),
                    mask=np.array([[[True, False]], [[True, False]]]),
                ),
                band_descriptions=["b0", "b1"],
            ),
        }
    )

    result = reduce_dimension(data=stack, reducer=reducer, dimension="bands")
    out = result.first.array

    assert isinstance(out, np.ma.MaskedArray)
    # Pixel (0,0) is nodata in both bands -> stays masked after band reduction.
    assert bool(np.ma.getmaskarray(out)[..., 0, 0]) is True
