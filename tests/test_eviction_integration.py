"""End-to-end A/B verification that intermediate eviction frees memory.

Drives a #300-shaped graph (load_collection -> ndvi -> aggregate_temporal -> save)
through the *real* graph engine with a synthetic in-memory load_collection, then
compares end-of-graph retained bytes with eviction off vs on.
"""

import gc
import weakref
from datetime import datetime

import numpy
import pytest
from openeo_pg_parser_networkx.graph import OpenEOProcessGraph
from openeo_pg_parser_networkx.process_registry import Process
from rio_tiler.models import ImageData

from titiler.openeo import results_cache as rc
from titiler.openeo.processes import PROCESS_SPECIFICATIONS, process_registry
from titiler.openeo.processes.implementations.data_model import RasterStack

N, H, W = 6, 256, 256  # 6 slices, 2-band uint16


def _retained_bytes(cache):
    """Sum the unique array bytes the results cache still pins (shared once)."""
    seen, total, count = set(), 0, 0
    for value in cache.values():
        data_cache = getattr(value, "_data_cache", {})
        for img in data_cache.values():
            arr = getattr(img, "array", None)
            if arr is None or id(arr) in seen:
                continue
            seen.add(id(arr))
            total += int(getattr(arr, "nbytes", 0))
            count += 1
    return total, count


def _fake_load_collection(id=None, named_parameters=None, **kwargs):
    """A regenerable load_collection backed by in-memory random slices."""

    def make(i):
        def task():
            arr = numpy.ma.MaskedArray(
                numpy.random.randint(1, 10000, (2, H, W)).astype("uint16"),
                mask=numpy.zeros((2, H, W), dtype=bool),
            )
            return ImageData(arr, bounds=(0, 0, 1, 1))

        return task

    tasks = [(make(i), {"datetime": datetime(2020, 1, 1 + i)}) for i in range(N)]
    return RasterStack(
        tasks=tasks,
        timestamp_fn=lambda a: a["datetime"],
        allowed_exceptions=(),
        width=W,
        height=H,
        bounds=(0, 0, 1, 1),
        band_names=["b1", "b2"],
    )


@pytest.fixture
def registered_load():
    """Register the synthetic load_collection, restoring the registry afterwards."""
    sentinel = object()
    try:
        previous = process_registry["load_collection"]
    except Exception:
        previous = sentinel

    process_registry["load_collection"] = Process(
        spec=PROCESS_SPECIFICATIONS["load_collection"],
        implementation=_fake_load_collection,
    )
    try:
        yield
    finally:
        if previous is sentinel:
            del process_registry["load_collection"]
        else:
            process_registry["load_collection"] = previous


_PG = {
    "process_graph": {
        "load": {"process_id": "load_collection", "arguments": {"id": "x"}},
        "ndvi": {
            "process_id": "ndvi",
            "arguments": {"data": {"from_node": "load"}, "nir": 2, "red": 1},
        },
        "agg": {
            "process_id": "aggregate_temporal",
            "arguments": {
                "data": {"from_node": "ndvi"},
                "intervals": [
                    ["2020-01-01", "2020-01-04"],
                    ["2020-01-04", "2020-02-01"],
                ],
                "reducer": {
                    "process_graph": {
                        "mean": {
                            "process_id": "mean",
                            "arguments": {"data": {"from_parameter": "data"}},
                            "result": True,
                        }
                    }
                },
            },
        },
        "save": {
            "process_id": "save_result",
            "arguments": {"data": {"from_node": "agg"}, "format": "GTIFF"},
            "result": True,
        },
    }
}


def _run(evict: bool, monkeypatch):
    monkeypatch.setenv(
        "TITILER_OPENEO_PROCESSING_EVICT_INTERMEDIATE_RESULTS",
        "true" if evict else "false",
    )
    graph = OpenEOProcessGraph(pg_data=_PG)
    cache = rc.make_results_cache(graph)
    fn = graph.to_callable(process_registry=process_registry, results_cache=cache)
    fn(named_parameters={})
    retained, n_arrays = _retained_bytes(cache)
    return cache, retained, n_arrays


def test_eviction_frees_intermediates(registered_load, monkeypatch):
    off_cache, off_bytes, off_arrays = _run(False, monkeypatch)
    on_cache, on_bytes, on_arrays = _run(True, monkeypatch)

    # Sanity: with eviction off the engine keeps every node's cube.
    assert type(off_cache) is dict
    assert isinstance(on_cache, rc.EvictingResultsCache)
    assert off_bytes > 0
    assert off_arrays >= N  # at least the source slices are retained

    # Eviction frees the source + index cubes once consumed, leaving ~nothing.
    assert on_bytes < off_bytes * 0.25
    assert on_arrays < off_arrays
    # A single source cube alone would be N*2*H*W*(2+1) bytes; on-retention must
    # be well under that (only the small final result may linger).
    one_cube = N * 2 * H * W * 3
    assert on_bytes < one_cube


_PG_LINEAR = {
    "process_graph": {
        "load": {"process_id": "load_collection", "arguments": {"id": "x"}},
        "ndvi": {
            "process_id": "ndvi",
            "arguments": {"data": {"from_node": "load"}, "nir": 2, "red": 1},
        },
        "save": {
            "process_id": "save_result",
            "arguments": {"data": {"from_node": "ndvi"}, "format": "GTIFF"},
            "result": True,
        },
    }
}


def _run_tracking_workflow(evict: bool, monkeypatch):
    """Run load -> ndvi -> save tracking weakrefs to the source ImageData.

    Returns the count still alive after the run while the graph (and thus
    ``graph.workflow``, which holds the engine's *second* reference to every
    result) is kept alive but the results_cache is dropped.
    """
    refs = []

    def tracking_load(id=None, named_parameters=None, **kwargs):
        def make(_i):
            def task():
                arr = numpy.ma.MaskedArray(
                    numpy.random.randint(1, 10000, (2, H, W)).astype("uint16"),
                    mask=numpy.zeros((2, H, W), dtype=bool),
                )
                img = ImageData(arr, bounds=(0, 0, 1, 1))
                refs.append(weakref.ref(img))
                return img

            return task

        tasks = [(make(i), {"datetime": datetime(2020, 1, 1 + i)}) for i in range(N)]
        return RasterStack(
            tasks=tasks,
            timestamp_fn=lambda a: a["datetime"],
            allowed_exceptions=(),
            width=W,
            height=H,
            bounds=(0, 0, 1, 1),
            band_names=["b1", "b2"],
        )

    sentinel = object()
    try:
        previous = process_registry["load_collection"]
    except Exception:
        previous = sentinel
    process_registry["load_collection"] = Process(
        spec=PROCESS_SPECIFICATIONS["load_collection"], implementation=tracking_load
    )
    try:
        monkeypatch.setenv(
            "TITILER_OPENEO_PROCESSING_EVICT_INTERMEDIATE_RESULTS",
            "true" if evict else "false",
        )
        graph = OpenEOProcessGraph(pg_data=_PG_LINEAR)
        cache = rc.make_results_cache(graph)
        fn = graph.to_callable(process_registry=process_registry, results_cache=cache)
        fn(named_parameters={})
        # Drop the cache/callable but KEEP `graph` alive: graph.workflow still
        # holds the second reference to each result (audit finding #2).
        del cache, fn
        gc.collect()
        alive = sum(1 for r in refs if r() is not None)
        del graph
        return alive
    finally:
        if previous is sentinel:
            del process_registry["load_collection"]
        else:
            process_registry["load_collection"] = previous


def test_eviction_defeats_workflow_double_retention(monkeypatch):
    """finding #2: the engine also stores each result on ``self.workflow``.

    Eviction must still free the cube, because release() empties the RasterStack
    in place rather than just dropping the results_cache reference.
    """
    # Without eviction, workflow alone keeps every source cube alive.
    assert _run_tracking_workflow(False, monkeypatch) == N
    # With eviction, the cubes are freed despite workflow holding the shells.
    assert _run_tracking_workflow(True, monkeypatch) == 0
