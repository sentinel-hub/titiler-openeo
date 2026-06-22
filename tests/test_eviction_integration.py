"""End-to-end A/B verification that intermediate eviction frees memory.

Drives a #300-shaped graph (load_collection -> ndvi -> aggregate_temporal -> save)
through the *real* graph engine with a synthetic in-memory load_collection, then
compares end-of-graph retained bytes with eviction off vs on. This is the
regression guard for EPIC #305 subtask 4 (results_cache eviction).
"""

from datetime import datetime

import numpy
import pytest
from openeo_pg_parser_networkx.graph import OpenEOProcessGraph
from openeo_pg_parser_networkx.process_registry import Process
from rio_tiler.models import ImageData

from titiler.openeo import results_cache as rc
from titiler.openeo.processes import PROCESS_SPECIFICATIONS, process_registry
from titiler.openeo.processes.implementations.data_model import RasterStack
from titiler.openeo.profiling import _sum_retained_bytes

N, H, W = 6, 256, 256  # 6 slices, 2-band uint16


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
    retained, n_arrays = _sum_retained_bytes(cache)
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
