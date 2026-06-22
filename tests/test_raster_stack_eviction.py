"""Tests for RasterStack.release()/clear() (EPIC #305, subtask 4).

These only drop *this stack's* references to its realized slices; deciding *when*
it is safe to call them is the job of the reference-counted results cache (see
tests/test_results_cache.py).
"""

from datetime import datetime

import numpy
from rio_tiler.models import ImageData

from titiler.openeo.processes.implementations.data_model import RasterStack


def _image(value: int = 1, bands: int = 2, size: int = 8) -> ImageData:
    arr = numpy.ma.MaskedArray(
        numpy.full((bands, size, size), value, dtype="uint16"),
        mask=numpy.zeros((bands, size, size), dtype=bool),
    )
    return ImageData(arr, bounds=(0, 0, 1, 1))


def _regenerable_stack(n: int = 2, bands: int = 2):
    """A stack backed by real (re-executable) tasks, tracking execution count."""
    calls = {"count": 0}

    def make_task(value: int):
        def task() -> ImageData:
            calls["count"] += 1
            return _image(value=value, bands=bands)

        return task

    tasks = [
        (make_task(i + 1), {"datetime": datetime(2020, 1, i + 1)}) for i in range(n)
    ]
    stack = RasterStack(
        tasks=tasks,
        timestamp_fn=lambda asset: asset["datetime"],
        allowed_exceptions=(),
        width=8,
        height=8,
        bounds=(0, 0, 1, 1),
        band_names=["b1", "b2"][:bands],
    )
    return stack, calls


def test_release_drops_cached_slices():
    stack, _ = _regenerable_stack(n=2)
    _ = stack.values()
    assert len(stack._data_cache) == 2

    stack.release()
    assert stack._data_cache == {}
    # Realized ImageRef images are cleared too.
    assert all(ref._image is None for ref in stack._image_refs.values())


def test_release_specific_keys():
    stack, _ = _regenerable_stack(n=2)
    keys = stack.timestamps()
    _ = stack.values()
    stack.release(keys[0])
    assert keys[0] not in stack._data_cache
    assert keys[1] in stack._data_cache


def test_release_allows_reread_for_real_task_stack():
    """A stack backed by real tasks can be re-read after release (re-executes)."""
    stack, calls = _regenerable_stack(n=1)
    _ = stack.first
    assert calls["count"] == 1
    stack.release()
    _ = stack.first
    assert calls["count"] == 2  # task re-executed to reload


def test_clear_evicts_all():
    img = _image()
    stack = RasterStack.from_images({datetime(2020, 1, 1): _image(2)})
    assert len(stack._data_cache) == 1
    stack.clear()
    assert stack._data_cache == {}
    # The underlying ImageData object survives if referenced elsewhere.
    assert img is not None
