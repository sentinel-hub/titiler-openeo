"""Tests for the optional memory-profiling harness (EPIC #305, subtask 1)."""

from datetime import datetime

import numpy
import pytest
from rio_tiler.models import ImageData

from titiler.openeo import profiling
from titiler.openeo.processes.implementations.data_model import RasterStack


@pytest.fixture(autouse=True)
def _reset_profiling():
    """Ensure the cached settings flag never leaks between tests."""
    profiling.reset_profiling_cache()
    yield
    profiling.reset_profiling_cache()


def _enable(monkeypatch):
    monkeypatch.setenv("TITILER_OPENEO_PROFILING_MEMORY", "true")
    profiling.reset_profiling_cache()


def _image(value: int = 1, bands: int = 1, size: int = 8) -> ImageData:
    arr = numpy.ma.MaskedArray(
        numpy.full((bands, size, size), value, dtype="uint16"),
        mask=numpy.zeros((bands, size, size), dtype=bool),
    )
    return ImageData(arr)


def test_disabled_by_default():
    """Profiling is off unless explicitly enabled."""
    assert profiling.memory_profiling_enabled() is False
    assert profiling.new_results_cache() is None


def test_enabled_via_env(monkeypatch):
    _enable(monkeypatch)
    assert profiling.memory_profiling_enabled() is True
    assert profiling.new_results_cache() == {}


def test_profile_node_is_noop_when_disabled(caplog):
    """No log output and no tracemalloc start when disabled."""
    with caplog.at_level("INFO", logger="titiler.openeo.profiling"):
        with profiling.profile_node("ndvi"):
            pass
    assert not [r for r in caplog.records if "[mem]" in r.message]


def test_profile_node_logs_when_enabled(monkeypatch, caplog):
    _enable(monkeypatch)
    with caplog.at_level("INFO", logger="titiler.openeo.profiling"):
        with profiling.profile_node("ndvi"):
            # Allocate something measurable so the delta is non-trivial.
            _block = numpy.ones((256, 256), dtype="float64")  # noqa: F841
    messages = [r.message for r in caplog.records]
    assert any("node=ndvi" in m and "heap_delta=" in m for m in messages)


def test_profile_node_tracks_depth(monkeypatch, caplog):
    """Nested process calls report increasing depth; top-level is depth 0."""
    _enable(monkeypatch)
    with caplog.at_level("INFO", logger="titiler.openeo.profiling"):
        with profiling.profile_node("aggregate_temporal"):
            with profiling.profile_node("mean"):
                pass
    msgs = [r.message for r in caplog.records if "node=" in r.message]
    assert any("node=mean depth=1" in m for m in msgs)
    assert any("node=aggregate_temporal depth=0" in m for m in msgs)


def test_retention_sums_unique_array_bytes(monkeypatch, caplog):
    """Shared ImageData across stacks is counted once, not double-counted."""
    _enable(monkeypatch)

    img = _image()
    key = datetime(2020, 1, 1)
    # Two stacks referencing the SAME ImageData (the audit's shared-reference case).
    stack_a = RasterStack.from_images({key: img})
    stack_b = RasterStack.from_images({key: img})

    results_cache = {"load": stack_a, "ndvi": stack_b}
    total, count = profiling._sum_retained_bytes(results_cache)

    assert count == 1  # same array object, counted once
    expected = img.array.data.nbytes + img.array.mask.nbytes
    assert total == expected

    with caplog.at_level("INFO", logger="titiler.openeo.profiling"):
        profiling.report_retention(results_cache, "test")
    assert any(
        "nodes_pinned=2" in r.message and "retained_arrays=1" in r.message
        for r in caplog.records
    )


def test_retention_counts_distinct_arrays(monkeypatch):
    _enable(monkeypatch)
    cache = {
        "a": RasterStack.from_images({datetime(2020, 1, 1): _image(1)}),
        "b": RasterStack.from_images({datetime(2020, 1, 2): _image(2)}),
    }
    total, count = profiling._sum_retained_bytes(cache)
    assert count == 2


def test_report_retention_noop_when_disabled(caplog):
    with caplog.at_level("INFO", logger="titiler.openeo.profiling"):
        profiling.report_retention({"a": _image()}, "test")
    assert not caplog.records


def test_array_bytes_includes_mask():
    img = _image(bands=2, size=16)
    nbytes = profiling._array_bytes(img.array)
    assert nbytes == img.array.data.nbytes + img.array.mask.nbytes


def test_iter_arrays_only_realized_slices(monkeypatch):
    """Lazy (unrealized) stack slices hold no pixel data and are skipped."""
    _enable(monkeypatch)
    stack = RasterStack.from_images({datetime(2020, 1, 1): _image()})
    arrays = list(profiling._iter_arrays(stack))
    assert len(arrays) == 1


def test_fmt_bytes():
    assert profiling._fmt_bytes(0) == "0.0B"
    assert profiling._fmt_bytes(1536) == "1.5KB"
    assert profiling._fmt_bytes(-2 * 1024 * 1024) == "-2.0MB"
    assert profiling._fmt_bytes(None) == "n/a"
