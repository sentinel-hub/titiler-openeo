"""Streaming ndvi/ndwi: bound the within-node peak (EPIC #305 subtask 7).

A sole-consumer source is streamed slice-by-slice and released as it's consumed,
so the whole source cube and the whole index cube are never both fully resident.
"""

from datetime import datetime

import numpy
from rio_tiler.models import ImageData

from titiler.openeo.processes.implementations.data_model import RasterStack
from titiler.openeo.processes.implementations.indices import ndvi


def _regenerable_stack(n=6, max_workers=2):
    """Deterministic regenerable stack: red=v, nir=2v -> ndvi == 1/3 everywhere."""
    calls = {"count": 0}

    def make(v):
        def task():
            calls["count"] += 1
            arr = numpy.ma.MaskedArray(
                numpy.stack(
                    [
                        numpy.full((8, 8), v, dtype="uint16"),  # band1 = red = v
                        numpy.full((8, 8), 2 * v, dtype="uint16"),  # band2 = nir = 2v
                    ]
                ),
                mask=numpy.zeros((2, 8, 8), dtype=bool),
            )
            return ImageData(arr, bounds=(0, 0, 1, 1))

        return task

    tasks = [(make(i + 1), {"datetime": datetime(2020, 1, 1 + i)}) for i in range(n)]
    stack = RasterStack(
        tasks=tasks,
        timestamp_fn=lambda a: a["datetime"],
        allowed_exceptions=(),
        max_workers=max_workers,
        width=8,
        height=8,
        bounds=(0, 0, 1, 1),
        band_names=["b1", "b2"],
    )
    return stack, calls


def test_streaming_releases_each_slice_and_is_correct():
    stack, calls = _regenerable_stack(n=6)
    stack._single_consumer = True  # tagged by the results cache in real graphs

    result = ndvi(stack, nir=2, red=1)

    assert len(result) == 6
    # Each source slice was loaded exactly once, then released.
    assert calls["count"] == 6
    assert stack._data_cache == {}
    # ndvi of (red=v, nir=2v) is 1/3 everywhere, float32.
    for img in result.values():
        assert img.array.dtype == numpy.float32
        assert numpy.allclose(img.array, 1.0 / 3.0)


def test_streaming_bounds_resident_slices_to_window():
    stack, _ = _regenerable_stack(n=6, max_workers=2)
    stack._single_consumer = True

    observed = []
    # Wrap the per-slice work to record how many slices are cached at once.
    import titiler.openeo.processes.implementations.indices as indices

    real_apply = indices._apply_ndvi

    def spy(img, nir, red):
        observed.append(len(stack._data_cache))
        return real_apply(img, nir, red)

    indices._apply_ndvi = spy
    try:
        ndvi(stack, nir=2, red=1)
    finally:
        indices._apply_ndvi = real_apply

    # Never more than the window (2) slices resident during processing.
    assert observed and max(observed) <= 2


def test_non_single_consumer_falls_back_and_keeps_source():
    """Without the tag the source may have other consumers: don't mutate it."""
    stack, _ = _regenerable_stack(n=4)
    # no _single_consumer attribute set -> fallback (data.items())
    result = ndvi(stack, nir=2, red=1)
    assert len(result) == 4
    # Fallback realizes the whole stack and leaves it cached (not released).
    assert len(stack._data_cache) == 4


def test_streaming_matches_non_streaming():
    streamed, _ = _regenerable_stack(n=5)
    streamed._single_consumer = True
    plain, _ = _regenerable_stack(n=5)

    r1 = ndvi(streamed, nir=2, red=1)
    r2 = ndvi(plain, nir=2, red=1)

    for k in r2.keys():
        assert numpy.array_equal(r1[k].array, r2[k].array)
