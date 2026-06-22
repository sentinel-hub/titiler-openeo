"""Reference-counted results cache that frees intermediates mid-graph.

The openEO graph engine stores every node's return value in a ``results_cache``
for the whole evaluation and never frees it, so peak memory grows with the *sum*
of all intermediate cubes rather than the working set (see the memory audit,
findings #1/#3, and EPIC #305 subtask 4).

:class:`EvictingResultsCache` is the same ``dict`` the engine already uses,
except it drops a node's heavy data as soon as the graph topology proves every
consumer of that node has run. Because we only free *after* the last consumer,
nothing ever re-reads an evicted result — there are no re-fetches.

Safety rules baked in:

* **Free only when fully consumed.** We count, per node, how many distinct
  consumer nodes reference its result (``ResultReference`` edges) and free it
  only once that count reaches zero.
* **Never free a still-reachable object.** If the same object is held under
  another cache slot (e.g. ``reduce_dimension`` returning its input unchanged),
  it is left alone — this also means a node that returns the final result is
  safe, since the result node is a consumer of whatever it returns.
* **Disable for re-executing graphs.** ``openeo_pg_parser_networkx``
  force-recomputes nodes that feed ``aggregate_spatial`` /
  ``aggregate_temporal_period``, which re-reads their inputs. If a graph contains
  any such node we disable eviction entirely and behave as a plain dict.
"""

import logging
from typing import Any, Dict, Set

from openeo_pg_parser_networkx.graph import OpenEOProcessGraph, PGEdgeType

logger = logging.getLogger(__name__)

# Processes whose presence makes the engine re-execute (and thus re-read) upstream
# nodes. Kept in sync with openeo_pg_parser_networkx's internal special-case; if
# the library changes this list, the worst case is that we keep more in memory
# (when we should disable) — never that we free something still needed, because
# the per-object reachability guard in `_maybe_release` is independent of it.
_RECOMPUTE_PROCESSES = frozenset({"aggregate_spatial", "aggregate_temporal_period"})


def _result_reference_parents(graph: OpenEOProcessGraph, node: Any) -> Set[Any]:
    """The nodes whose results ``node`` consumes (its data dependencies)."""
    return {
        source
        for _, source, data in graph.G.out_edges(node, data=True)
        if data.get("reference_type") == PGEdgeType.ResultReference
    }


def _graph_has_recompute(graph: OpenEOProcessGraph) -> bool:
    return any(
        data.get("process_id") in _RECOMPUTE_PROCESSES
        for _node, data in graph.G.nodes(data=True)
    )


def _release_value(value: Any) -> None:
    """Free a result's heavy data if it knows how (RasterStack.release())."""
    release = getattr(value, "release", None)
    if not callable(release):
        return
    try:
        release()
    except (
        Exception
    ) as exc:  # pragma: no cover - defensive; eviction must never break a graph
        logger.debug("results_cache: release() failed, leaving value in place: %s", exc)


class EvictingResultsCache(dict):
    """``results_cache`` that frees a node's data once all consumers have run."""

    def __init__(self, graph: OpenEOProcessGraph) -> None:
        """Precompute per-node consumer counts from the graph topology."""
        super().__init__()
        self._enabled = not _graph_has_recompute(graph)
        # remaining[n] = number of consumer nodes that still need n's result.
        self._remaining: Dict[Any, int] = {}
        # parents[n] = the nodes n consumes (counted once each).
        self._parents: Dict[Any, Set[Any]] = {}
        # nodes whose result has already been stored (decrement parents once).
        self._stored: Set[Any] = set()

        if self._enabled:
            for node in graph.G.nodes():
                parents = _result_reference_parents(graph, node)
                self._parents[node] = parents
                for parent in parents:
                    self._remaining[parent] = self._remaining.get(parent, 0) + 1

    def __setitem__(self, node: Any, value: Any) -> None:
        """Store a node's result and free any parent whose last consumer this is."""
        super().__setitem__(node, value)
        if not self._enabled or node in self._stored:
            # The engine may re-store a node (e.g. recompute); only the first
            # store represents this node consuming its parents.
            return
        self._stored.add(node)
        for parent in self._parents.get(node, ()):
            self._remaining[parent] = self._remaining.get(parent, 0) - 1
            if self._remaining[parent] <= 0:
                self._maybe_release(parent)

    def _maybe_release(self, node: Any) -> None:
        if node not in self:
            return
        value = self[node]
        # Don't free an object still reachable through another cache slot
        # (identity passthrough such as reduce_dimension returning its input,
        # or the final result node holding it).
        if sum(1 for v in self.values() if v is value) > 1:
            return
        _release_value(value)


def make_results_cache(graph: OpenEOProcessGraph) -> Dict[Any, Any]:
    """Build the results cache for a graph evaluation.

    Returns an :class:`EvictingResultsCache` when intermediate eviction is
    enabled (the default; see ``ProcessingSettings.evict_intermediate_results``),
    otherwise a plain ``dict`` with the engine's original keep-everything
    behavior. Pass the result to ``OpenEOProcessGraph.to_callable(results_cache=)``.
    """
    from .settings import ProcessingSettings

    if not ProcessingSettings().evict_intermediate_results:
        return {}
    return EvictingResultsCache(graph)
