"""Tests for the reader-requirement planner (issue #348, ADR 0002 §2.6,
increments 5-6).

`titiler.openeo.reader_requirements` is the production module the increment-4
spike (`tests/test_reader_requirement_channel_spike.py`) prototyped: a
requirement registry, a pre-execution DAG pass keyed by a `load_collection`
node's own `(id, bands)` signature (not node identity -- proven
indistinguishable at call time in increment 4), and a per-request process
registry using the copy recipe increment 4 verified is the only safe one.

`_REQUIREMENT_PROVIDERS` shipped empty in increment 5 and got its first (and
so far only) entry in increment 6: `sar_backscatter`, registered as an import
side effect of `processes/implementations/sar.py` -- imported explicitly
below so that registration has happened regardless of what other test files
did or didn't import first. Most of these tests still register their own
*synthetic* provider via monkeypatch to exercise the mechanism in isolation
from `sar_backscatter`'s real one.
"""

from typing import Any, Dict

import pytest
from openeo_pg_parser_networkx import OpenEOProcessGraph
from openeo_pg_parser_networkx.process_registry import Process, ProcessRegistry

import titiler.openeo.processes.implementations.sar  # noqa: F401 -- registers sar_backscatter
from titiler.openeo import reader_requirements as rr

_NEEDS_LUT = "__test_only_requiring_process__"


def _needs_lut_provider(
    resolved_kwargs: Dict[str, Any], load_collection_kwargs: Dict[str, Any]
) -> rr.Requirement:
    return rr.Requirement(extra_bands=frozenset({"vv_sigma0_lut"}))


@pytest.fixture
def register_needs_lut(monkeypatch):
    """Register a synthetic requiring process for the duration of a test."""
    monkeypatch.setitem(rr._REQUIREMENT_PROVIDERS, _NEEDS_LUT, _needs_lut_provider)


def _two_load_collection_graph(same_collection: bool) -> OpenEOProcessGraph:
    """lc_a feeds the synthetic requiring process (needs the LUT band); lc_b
    feeds a plain reduce_dimension (needs nothing). `same_collection` toggles
    whether both nodes share an id/bands signature (the ambiguous case ADR
    0002 §3.1 documents as the channel's structural default) or load
    different collections (the case a signature-blind dispatch would get
    wrong)."""
    lc_b_id = "sentinel-1-grd" if same_collection else "sentinel-2-l2a"
    lc_b_bands = ["vv", "vh"] if same_collection else ["B04", "B03", "B02"]
    pg = {
        "lc_a": {
            "process_id": "load_collection",
            "arguments": {"id": "sentinel-1-grd", "bands": ["vv", "vh"]},
        },
        "lc_b": {
            "process_id": "load_collection",
            "arguments": {"id": lc_b_id, "bands": lc_b_bands},
        },
        "needs_lut": {
            "process_id": _NEEDS_LUT,
            "arguments": {"data": {"from_node": "lc_a"}},
        },
        "reduced": {
            "process_id": "reduce_dimension",
            "arguments": {"data": {"from_node": "lc_b"}, "dimension": "t"},
        },
        "merged": {
            "process_id": "merge_cubes",
            "arguments": {
                "cube1": {"from_node": "needs_lut"},
                "cube2": {"from_node": "reduced"},
            },
        },
        "combined": {
            "process_id": "save_result",
            "arguments": {"data": {"from_node": "merged"}, "format": "GTiff"},
            "result": True,
        },
    }
    return OpenEOProcessGraph(pg_data=pg)


def _load_collection_impl(id, bands=None, named_parameters=None, **kwargs):
    return f"cube({id},{bands})"


def _base_registry() -> ProcessRegistry:
    registry = ProcessRegistry()
    registry["load_collection"] = Process(spec={}, implementation=_load_collection_impl)
    registry[_NEEDS_LUT] = Process(
        spec={}, implementation=lambda data, **kw: f"needs_lut({data})"
    )
    registry["reduce_dimension"] = Process(
        spec={}, implementation=lambda data, **kw: f"reduced({data})"
    )
    registry["merge_cubes"] = Process(
        spec={}, implementation=lambda cube1, cube2, **kw: f"merged({cube1},{cube2})"
    )
    registry["save_result"] = Process(
        spec={}, implementation=lambda data, **kw: f"saved({data})"
    )
    return registry


# ---------------------------------------------------------------------------
# Shipped state: sar_backscatter is the one registered provider; anything
# else is still a no-op.
# ---------------------------------------------------------------------------


def test_requirement_providers_contains_exactly_sar_backscatter():
    """sar_backscatter is increment 6's convergence -- the first and, as of
    this writing, only registered provider. If this test breaks because a new
    process was registered, update it deliberately rather than widen it
    blindly."""
    assert set(rr._REQUIREMENT_PROVIDERS) == {"sar_backscatter"}


def test_resolve_requirements_is_empty_when_no_registered_provider_matches():
    """None of this synthetic graph's process ids (including sar_backscatter,
    the one real registered provider) appear in it, so nothing matches."""
    graph = _two_load_collection_graph(same_collection=False)
    assert rr.resolve_requirements(graph) == {}


def test_build_per_request_registry_returns_the_same_object_when_nothing_to_inject():
    """The strongest form of the increment-5 gate: 'a graph with no requiring
    process must produce a byte-identical read' -- here, literally the same
    registry object, not just equivalent behavior."""
    base = _base_registry()
    assert rr.build_per_request_registry(base, {}) is base


def test_plan_process_registry_is_a_noop_for_graphs_sar_backscatter_never_touches():
    """End to end, against the real (non-empty) provider registry: a graph
    that never uses sar_backscatter still gets its registry back unchanged."""
    graph = _two_load_collection_graph(same_collection=False)
    base = _base_registry()
    assert rr.plan_process_registry(graph, base) is base


# ---------------------------------------------------------------------------
# The mechanism itself, exercised via a synthetic requiring process.
# ---------------------------------------------------------------------------


def test_signature_keyed_injection_serves_the_requiring_node_only(register_needs_lut):
    graph = _two_load_collection_graph(same_collection=False)
    requirements = rr.resolve_requirements(graph)
    assert requirements == {
        ("sentinel-1-grd", ("vv", "vh")): rr.Requirement(
            extra_bands=frozenset({"vv_sigma0_lut"})
        )
    }

    base = _base_registry()
    registry = rr.build_per_request_registry(base, requirements)
    assert registry is not base  # a real rebind happened, not the no-op path

    result = graph.to_callable(process_registry=registry)()

    assert "cube(sentinel-1-grd,['vv', 'vh', 'vv_sigma0_lut'])" in result
    assert "cube(sentinel-2-l2a,['B04', 'B03', 'B02'])" in result
    assert "vv_sigma0_lut" not in result.split("cube(sentinel-2-l2a,")[1]


def test_signature_keyed_injection_unions_same_signature_siblings(register_needs_lut):
    """Documented, deliberate limitation (ADR 0002 §3.1): two load_collection
    nodes sharing an (id, bands) signature can't be told apart at call time,
    so a requirement resolved for one reaches both."""
    graph = _two_load_collection_graph(same_collection=True)
    requirements = rr.resolve_requirements(graph)

    registry = rr.build_per_request_registry(_base_registry(), requirements)
    result = graph.to_callable(process_registry=registry)()

    assert result.count("vv_sigma0_lut") == 2


def test_isolated_copy_does_not_mutate_the_base_registry(register_needs_lut):
    """Probe with the *vulnerable* signature -- the one that has a requirement
    attached. A probe with an unrelated signature would pass even under a
    contaminated (unisolated) copy, because the rebound closure still
    delegates correctly for keys it finds no requirement for; that would
    make this test worthless as a regression guard."""
    graph = _two_load_collection_graph(same_collection=False)
    requirements = rr.resolve_requirements(graph)
    assert ("sentinel-1-grd", ("vv", "vh")) in requirements  # sanity: real requirement

    base = _base_registry()
    original_implementation = base["load_collection"].implementation
    rr.build_per_request_registry(base, requirements)

    # The shared registry a later, unrelated request would use is untouched:
    # same implementation object, and calling it for the signature that *does*
    # carry a requirement must not inject anything.
    assert base["load_collection"].implementation is original_implementation
    assert (
        base["load_collection"].implementation(id="sentinel-1-grd", bands=["vv", "vh"])
        == "cube(sentinel-1-grd,['vv', 'vh'])"
    )


def test_resolved_requirement_is_logged(register_needs_lut, caplog):
    import logging

    graph = _two_load_collection_graph(same_collection=False)
    requirements = rr.resolve_requirements(graph)
    registry = rr.build_per_request_registry(_base_registry(), requirements)

    with caplog.at_level(logging.INFO, logger="titiler.openeo.reader_requirements"):
        graph.to_callable(process_registry=registry)()

    messages = [r.message for r in caplog.records]
    assert any("sentinel-1-grd" in m and "vv_sigma0_lut" in m for m in messages)
    assert not any("sentinel-2-l2a" in m for m in messages)  # untouched, not logged


# ---------------------------------------------------------------------------
# _signature_key: the boundary the whole mechanism depends on.
# ---------------------------------------------------------------------------


def test_signature_key_happy_path():
    assert rr._signature_key("sentinel-1-grd", ["vv", "vh"]) == (
        "sentinel-1-grd",
        ("vv", "vh"),
    )
    assert rr._signature_key("sentinel-1-grd", None) == ("sentinel-1-grd", ())


def test_signature_key_none_for_unresolved_or_malformed_values():
    """A UDP `from_parameter` reference is still unresolved in a node's static
    resolved_kwargs at graph-construction time (resolution happens inside
    load_collection's own body, at call time). Such values aren't plain
    str/list -- the key must be None, not a wrong guess."""
    unresolved_id = object()
    assert rr._signature_key(unresolved_id, ["vv"]) is None
    assert rr._signature_key("sentinel-1-grd", "not-a-list") is None
    assert rr._signature_key("sentinel-1-grd", [1, 2]) is None


# ---------------------------------------------------------------------------
# _merge_bands: bands=None must not be treated as "all bands".
# ---------------------------------------------------------------------------


def test_merge_bands_treats_none_as_empty_not_all_bands():
    """`LoadCollection.load_collection` defaults a missing `bands` to the
    collection's first asset key, not "everything" -- so injection must
    materialize an explicit list rather than leaving None untouched."""
    assert rr._merge_bands(None, frozenset({"vv_sigma0_lut"})) == ["vv_sigma0_lut"]


def test_merge_bands_does_not_duplicate_already_requested_bands():
    assert rr._merge_bands(["vv", "vh"], frozenset({"vh", "vv_sigma0_lut"})) == [
        "vv",
        "vh",
        "vv_sigma0_lut",
    ]
