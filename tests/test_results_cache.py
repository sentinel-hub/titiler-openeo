"""Tests for the reference-counted results cache."""

from datetime import datetime

import numpy
from openeo_pg_parser_networkx.graph import OpenEOProcessGraph
from rio_tiler.models import ImageData

from titiler.openeo import results_cache as rc
from titiler.openeo.processes.implementations.data_model import RasterStack
from titiler.openeo.results_cache import EvictingResultsCache, make_results_cache


def _stack(value: int = 1) -> RasterStack:
    arr = numpy.ma.MaskedArray(
        numpy.full((1, 8, 8), value, dtype="uint16"),
        mask=numpy.zeros((1, 8, 8), dtype=bool),
    )
    return RasterStack.from_images({datetime(2020, 1, 1): ImageData(arr)})


def _graph(pg: dict):
    """Parse a process graph and return (graph, {prefix: node_id})."""
    g = OpenEOProcessGraph(pg_data={"process_graph": pg})
    nodes = {nid.split("-", 1)[0]: nid for nid in g.G.nodes()}
    return g, nodes


_LINEAR = {
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


def test_frees_input_once_consumer_runs():
    """In load -> ndvi -> save, `load` is freed when `ndvi` is stored."""
    g, nodes = _graph(_LINEAR)
    cache = EvictingResultsCache(g)
    assert cache._enabled is True

    load_val, ndvi_val = _stack(1), _stack(2)

    # Engine stores parents before consumers.
    cache[nodes["load"]] = load_val
    assert len(load_val._data_cache) == 1  # still held

    cache[nodes["ndvi"]] = ndvi_val  # ndvi consumes load -> load freed
    assert load_val._data_cache == {}
    assert len(ndvi_val._data_cache) == 1  # ndvi result still held

    cache[nodes["root"]] = b"bytes"  # save consumes ndvi -> ndvi freed
    assert ndvi_val._data_cache == {}


def test_result_node_value_not_freed():
    """A value held by the (consumer) result node is never freed."""
    g, nodes = _graph(_LINEAR)
    cache = EvictingResultsCache(g)
    val = _stack(1)
    # Final node returns the value directly (identity); it must survive.
    cache[nodes["load"]] = _stack(9)
    cache[nodes["ndvi"]] = val
    cache[nodes["root"]] = val  # aliased under the result node
    # ndvi's slot is freed only if not reachable elsewhere; here `val` is also
    # the result, so it must stay intact.
    assert len(val._data_cache) == 1


def test_alias_is_not_freed():
    """An object reachable via another cache slot is not released."""
    g, nodes = _graph(_LINEAR)
    cache = EvictingResultsCache(g)
    shared = _stack(1)
    cache[nodes["load"]] = shared
    cache[nodes["ndvi"]] = shared  # passthrough: same object under two keys
    # Storing ndvi decrements load to 0, but `shared` is aliased -> kept.
    assert len(shared._data_cache) == 1


def test_disabled_for_recompute_graphs():
    """Graphs with aggregate_spatial disable eviction (engine re-reads inputs)."""
    pg = {
        "load": {"process_id": "load_collection", "arguments": {"id": "x"}},
        "agg": {
            "process_id": "aggregate_spatial",
            "arguments": {
                "data": {"from_node": "load"},
                "geometries": {"type": "Point", "coordinates": [0, 0]},
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
            "arguments": {"data": {"from_node": "agg"}, "format": "JSON"},
            "result": True,
        },
    }
    g, nodes = _graph(pg)
    assert rc._graph_has_recompute(g) is True

    cache = EvictingResultsCache(g)
    assert cache._enabled is False
    val = _stack(1)
    cache[nodes["load"]] = val
    cache[nodes["agg"]] = _stack(2)
    assert len(val._data_cache) == 1  # nothing freed


def test_make_results_cache_respects_setting(monkeypatch):
    g, _ = _graph(_LINEAR)

    monkeypatch.setenv("TITILER_OPENEO_PROCESSING_EVICT_INTERMEDIATE_RESULTS", "false")
    plain = make_results_cache(g)
    assert type(plain) is dict

    monkeypatch.setenv("TITILER_OPENEO_PROCESSING_EVICT_INTERMEDIATE_RESULTS", "true")
    evicting = make_results_cache(g)
    assert isinstance(evicting, EvictingResultsCache)


def test_eviction_is_robust_to_restore():
    """Re-storing a node (engine recompute) must not double-decrement parents."""
    g, nodes = _graph(_LINEAR)
    cache = EvictingResultsCache(g)
    cache[nodes["load"]] = _stack(1)
    ndvi_val = _stack(2)
    cache[nodes["ndvi"]] = ndvi_val
    # A second store of the same node (idempotent) must not touch ndvi again.
    cache[nodes["ndvi"]] = ndvi_val
    assert len(ndvi_val._data_cache) == 1
