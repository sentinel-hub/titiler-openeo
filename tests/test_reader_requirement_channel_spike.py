"""Increment 4 spike (issue #348, ADR 0002 S3.1 / S4): settles the two open
questions about the reader-requirement channel ADR 0001 S7.10(b) chose --
a per-request process registry with `load_collection` rebound -- before the
planner (increments 5-6) is built on top of it.

1. Does rebinding `load_collection` in a shallow-copied process registry
   survive `openeo_pg_parser_networkx`'s `Process` wrapping? Yes, but only if
   the copy actually isolates the registry's storage. `copy.copy(registry)`
   -- the literal reading of "a shallow copy" -- copies the `ProcessRegistry`
   object's attributes by reference, so `registry.store` (and each per-namespace
   dict inside it) is *shared*, not copied. Rebinding "load_collection" on the
   copy mutates the dict the original, application-lifetime-scoped registry
   also reads from: every later request, not just concurrent ones, would see
   the rebind. See `test_naive_copy_copy_leaks_rebind_into_shared_registry`
   (documents the trap) and `test_isolated_copy_rebinds_without_leaking` (the
   corrected recipe: copy `store` one level deeper).

2. How does one rebound callable serve several `load_collection` nodes with
   different requirements, when S7.10(b) asserts nodes "are resolved
   independently"? Not by node identity -- there isn't one to dispatch on.
   `OpenEOProcessGraph._map_node_to_callable` looks up
   `process_registry[process_id].implementation` once per node at
   graph-construction time and bakes that node's own `resolved_kwargs` into a
   `functools.partial`; the callable itself receives only those baked kwargs
   at call time, nothing that identifies which graph node it is being called
   for. `test_identical_signature_nodes_are_indistinguishable_at_call_time`
   proves two nodes with the same `id`/`bands` are indistinguishable from
   inside the callable. The sound channel is therefore a signature-keyed
   union -- key resolved requirements by `(id, tuple(bands))` and union across
   every node sharing a key -- not per-node injection and not an unconditional
   "apply the one resolved requirement to every call", which
   `test_naive_unconditional_dispatch_contaminates_unrelated_collection` shows
   corrupts an unrelated `load_collection` node that happens to share the
   callable. `test_signature_keyed_dispatch_serves_both_nodes` is the
   corrected channel.

Decision recorded in docs/adr/0002-band-sources.md S3.1 and the increment 4
row of S4. This file is the spike's proof, not the increment 5 planner: the
helpers below are deliberately local and minimal, standing in for the real
requirement registry / DAG pass increment 5 builds.
"""

import copy
from dataclasses import dataclass
from typing import Any, Dict, FrozenSet, Tuple

import networkx as nx
from openeo_pg_parser_networkx import OpenEOProcessGraph
from openeo_pg_parser_networkx.process_registry import Process, ProcessRegistry

# ---------------------------------------------------------------------------
# Question 1: does the copy actually isolate the registry?
# ---------------------------------------------------------------------------


def test_naive_copy_copy_leaks_rebind_into_shared_registry():
    """`copy.copy(registry)` shares `registry.store` by reference, so rebinding
    the "copy" rebinds the original too -- the literal ADR wording is a trap."""
    registry = ProcessRegistry()
    registry["load_collection"] = Process(
        spec={}, implementation=lambda **kw: "original"
    )

    per_request = copy.copy(registry)
    per_request["load_collection"] = Process(
        spec={}, implementation=lambda **kw: "rebound"
    )

    # The bug: the shared, application-lifetime registry is now permanently
    # rebound too, for every request that follows -- not just concurrent ones.
    assert registry["load_collection"].implementation() == "rebound"


def _isolated_copy(registry: ProcessRegistry) -> ProcessRegistry:
    """The corrected per-request recipe: copy the registry object *and* its
    per-namespace dicts, so `__setitem__` on the copy can never mutate the
    original. Individual `Process` objects for untouched process ids are still
    shared -- fine, since nothing mutates a `Process` in place."""
    isolated = copy.copy(registry)
    isolated.store = {ns: dict(procs) for ns, procs in registry.store.items()}
    return isolated


def test_isolated_copy_rebinds_without_leaking():
    """The corrected recipe rebinds `load_collection` for the request only,
    leaving the shared registry -- and its other entries -- untouched. This is
    the answer to "does rebinding survive Process wrapping": yes, `__setitem__`
    re-applies `wrap_funcs` to the new implementation exactly as it does for
    any other registration, once the copy is isolated enough for the rebind
    not to be a global mutation."""
    calls = []

    def wrap(fn):
        def wrapped(**kw):
            calls.append("wrapped")
            return fn(**kw)

        return wrapped

    registry = ProcessRegistry(wrap_funcs=[wrap])
    registry["load_collection"] = Process(
        spec={}, implementation=lambda **kw: "original"
    )
    registry["save_result"] = Process(spec={}, implementation=lambda **kw: "unrelated")

    per_request = _isolated_copy(registry)
    per_request["load_collection"] = Process(
        spec={}, implementation=lambda **kw: "rebound"
    )

    assert per_request["load_collection"].implementation() == "rebound"
    assert calls == ["wrapped"]  # the rebind went through the same wrap_funcs
    assert registry["load_collection"].implementation() == "original"  # not leaked
    assert per_request["save_result"].implementation() == "unrelated"  # untouched


# ---------------------------------------------------------------------------
# Question 2: can the callable tell nodes apart?
# ---------------------------------------------------------------------------


def _two_load_collection_graph(same_collection: bool) -> OpenEOProcessGraph:
    """lc_a feeds sar_backscatter (needs the sigma0 LUT band); lc_b feeds a
    plain reduce_dimension (needs nothing extra). `same_collection` controls
    whether both nodes load the same id/bands (the ambiguous case, S3.1's
    first open risk) or different collections (the case a *global* dispatch
    gets wrong)."""
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
        "sar": {
            "process_id": "sar_backscatter",
            "arguments": {
                "data": {"from_node": "lc_a"},
                "coefficient": "sigma0-ellipsoid",
            },
        },
        "reduced": {
            "process_id": "reduce_dimension",
            "arguments": {"data": {"from_node": "lc_b"}, "dimension": "t"},
        },
        "merged": {
            "process_id": "merge_cubes",
            "arguments": {
                "cube1": {"from_node": "sar"},
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


def _base_registry() -> ProcessRegistry:
    registry = ProcessRegistry()
    registry["sar_backscatter"] = Process(
        spec={}, implementation=lambda data, **kw: f"sar({data})"
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


def test_identical_signature_nodes_are_indistinguishable_at_call_time():
    """Node identity never reaches the rebound callable. Two load_collection
    nodes with the same id/bands produce literally identical call-time kwargs,
    even though one feeds sar_backscatter and the other doesn't -- confirming
    S7.10(b)'s "resolved independently" cannot mean "distinguished at call
    time"; independence can only happen earlier, during graph planning."""
    graph = _two_load_collection_graph(same_collection=True)
    calls = []

    def load_collection_impl(id, bands=None, named_parameters=None, **kwargs):
        calls.append({"id": id, "bands": tuple(bands or ())})
        return f"cube({id},{bands})"

    registry = _base_registry()
    registry["load_collection"] = Process(spec={}, implementation=load_collection_impl)

    graph.to_callable(process_registry=registry)()

    assert len(calls) == 2
    assert calls[0] == calls[1]  # nothing at call time tells them apart


@dataclass(frozen=True)
class _Requirement:
    """Stand-in for increment 5's real value object -- just enough to prove
    the dispatch shape works."""

    extra_bands: FrozenSet[str] = frozenset()

    def __or__(self, other: "_Requirement") -> "_Requirement":
        return _Requirement(extra_bands=self.extra_bands | other.extra_bands)


_NONE = _Requirement()


def _resolve_requirements_by_signature(
    graph: OpenEOProcessGraph,
) -> Dict[Tuple[Any, Tuple[str, ...]], _Requirement]:
    """The pre-execution pass from ADR 0002 S2.6.2, keyed by each
    load_collection node's own resolved_kwargs signature -- the only key the
    rebound callable can observe at call time (see the test above) -- instead
    of by node identity. Nodes sharing a signature have their requirements
    unioned, per S3.1's "conservative union across nodes" candidate."""
    requirements: Dict[Tuple[Any, Tuple[str, ...]], _Requirement] = {}
    for node in graph.G.nodes:
        data = graph.G.nodes[node]
        if data["process_id"] != "load_collection":
            continue
        ancestor_processes = {
            graph.G.nodes[a]["process_id"] for a in nx.ancestors(graph.G, node)
        }
        req = (
            _Requirement(extra_bands=frozenset({"vv_sigma0_lut"}))
            if "sar_backscatter" in ancestor_processes
            else _NONE
        )
        kwargs = data["resolved_kwargs"]
        key = (kwargs["id"], tuple(kwargs.get("bands") or ()))
        requirements[key] = requirements.get(key, _NONE) | req
    return requirements


def _rebind_with_unconditional_requirement(
    registry: ProcessRegistry, requirement: _Requirement
) -> ProcessRegistry:
    """The naive reading of "one rebound callable": apply the single resolved
    requirement to every load_collection call, regardless of which collection
    it is. This is what `test_naive_unconditional_dispatch_...` shows is
    wrong."""

    def rebound(id, bands=None, named_parameters=None, **kwargs):
        merged = list(bands or []) + [
            b for b in sorted(requirement.extra_bands) if b not in (bands or [])
        ]
        return f"cube({id},{merged})"

    isolated = _isolated_copy(registry)
    isolated["load_collection"] = Process(spec={}, implementation=rebound)
    return isolated


def _rebind_with_signature_dispatch(
    registry: ProcessRegistry,
    requirements: Dict[Tuple[Any, Tuple[str, ...]], _Requirement],
) -> ProcessRegistry:
    """The corrected channel: the one rebound callable looks up the requirement
    for *its own call-time signature* before deciding what to inject."""

    def rebound(id, bands=None, named_parameters=None, **kwargs):
        key = (id, tuple(bands or ()))
        requirement = requirements.get(key, _NONE)
        merged = list(bands or []) + [
            b for b in sorted(requirement.extra_bands) if b not in (bands or [])
        ]
        return f"cube({id},{merged})"

    isolated = _isolated_copy(registry)
    isolated["load_collection"] = Process(spec={}, implementation=rebound)
    return isolated


def test_naive_unconditional_dispatch_contaminates_unrelated_collection():
    """lc_a (sentinel-1-grd) needs the LUT band; lc_b (sentinel-2-l2a) is an
    unrelated collection that must not receive it. Applying the one resolved
    requirement to every load_collection call -- the naive reading of "one
    shared rebound callable" -- injects the SAR LUT band into the Sentinel-2
    load too. This is the failing case the signature-keyed channel exists to
    avoid."""
    graph = _two_load_collection_graph(same_collection=False)
    requirements = _resolve_requirements_by_signature(graph)
    single_requirement = next(iter(requirements.values()))

    registry = _rebind_with_unconditional_requirement(
        _base_registry(), single_requirement
    )
    result = graph.to_callable(process_registry=registry)()

    assert "cube(sentinel-1-grd,['vv', 'vh', 'vv_sigma0_lut'])" in result
    # The bug: an optical collection now carries a SAR-only band name.
    assert "vv_sigma0_lut" in result.split("cube(sentinel-2-l2a,")[1]


def test_signature_keyed_dispatch_serves_both_nodes():
    """The corrected channel: one rebound `load_collection` callable, shared by
    both nodes, injects the LUT band only for the signature that needs it.
    sentinel-1-grd gets `vv_sigma0_lut`; sentinel-2-l2a is untouched."""
    graph = _two_load_collection_graph(same_collection=False)
    requirements = _resolve_requirements_by_signature(graph)

    registry = _rebind_with_signature_dispatch(_base_registry(), requirements)
    result = graph.to_callable(process_registry=registry)()

    assert "cube(sentinel-1-grd,['vv', 'vh', 'vv_sigma0_lut'])" in result
    assert "cube(sentinel-2-l2a,['B04', 'B03', 'B02'])" in result
    assert "vv_sigma0_lut" not in result.split("cube(sentinel-2-l2a,")[1]


def test_signature_keyed_dispatch_unions_same_signature_nodes():
    """S3.1's residual, explicitly deferred risk: when two nodes *do* share a
    signature (test_identical_signature_nodes_are_indistinguishable_at_call_time),
    the signature-keyed channel cannot tell them apart either, so the union
    reaches both -- reduce_dimension's input picks up a band it never asked
    for. Increment 5 decides whether to restrict injection to single-consumer
    load_collection nodes or strip injected bands post-mosaic for non-requiring
    consumers (ADR 0002 S3.1); this test just pins down that the contamination
    is real and bounded to same-signature siblings, not global."""
    graph = _two_load_collection_graph(same_collection=True)
    requirements = _resolve_requirements_by_signature(graph)

    registry = _rebind_with_signature_dispatch(_base_registry(), requirements)
    result = graph.to_callable(process_registry=registry)()

    # Both branches read sentinel-1-grd,['vv','vh'] -- the union reaches both.
    assert result.count("vv_sigma0_lut") == 2
