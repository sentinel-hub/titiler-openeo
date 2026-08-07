"""Reader-requirement planner: lets a downstream process declare that a
``load_collection`` node feeding it must produce extra bands, without the
process graph itself changing (ADR 0002 §2.6, extending ADR 0001 §7.10(b)).

Mechanism, mirroring :mod:`titiler.openeo.results_cache`'s shape:

1. A **requirement registry** (:data:`_REQUIREMENT_PROVIDERS`, populated via
   :func:`register_requirement_provider`), keyed by process id like
   ``_RECOMPUTE_PROCESSES``, but valued by callables
   ``(resolved_kwargs, load_collection_kwargs) -> Requirement`` rather than
   constants, because what a process needs can depend on its own arguments
   (e.g. ``sar_backscatter``'s required LUT depends on its ``coefficient``)
   *and* on the ancestor ``load_collection`` node's own arguments (e.g. which
   polarisations were requested) -- the requiring process's own
   ``resolved_kwargs`` alone doesn't carry that.
2. A **pre-execution pass** (:func:`resolve_requirements`) over the parsed
   DAG: for each ``load_collection`` node, union the requirements of every
   process in its downstream cone (``nx.ancestors`` -- edges point from a
   consumer to what it consumes, so a load node's ancestors are its
   consumers, transitively; ADR 0002 §1.6).
3. A **per-request process registry** (:func:`build_per_request_registry`)
   with ``load_collection`` rebound to inject the resolved bands.

Increment 4 (``tests/test_reader_requirement_channel_spike.py``, ADR 0002
§3.1) settled two things this module must respect:

* The per-request copy must isolate ``ProcessRegistry.store`` one
  namespace-dict level deeper than ``copy.copy()`` alone reaches --
  otherwise rebinding "the copy" mutates the real, application-lifetime
  registry for every later request, not just concurrent ones.
* The rebound callable has no per-graph-node identity at call time, only
  the ``id``/``bands`` it was itself called with. Two ``load_collection``
  nodes sharing a signature are indistinguishable, so resolved requirements
  are keyed by that ``(id, tuple(bands))`` signature and **unioned** across
  every node sharing one -- not resolved independently per node, and never
  applied unconditionally to every call regardless of signature (proven to
  contaminate an unrelated collection).

:data:`_REQUIREMENT_PROVIDERS` shipped empty in increment 5 (no process had
converged onto this mechanism yet) and got its first entry in increment 6:
``sar_backscatter`` (registered from ``processes/implementations/sar.py`` at
import time, via :func:`register_requirement_provider` rather than reaching
into this module's dict directly -- this module stays fully process-agnostic,
per ADR 0002 §2.1's "never a band-source-specific plugin system" principle).
A graph containing no process with a registered provider still produces
``{}`` from :func:`resolve_requirements`, which still makes
:func:`build_per_request_registry` return the *original* registry object,
unchanged -- "a graph with no requiring process must produce a byte-identical
read" (increment 5's gate) is a property of an empty match, not of an empty
registry, so it keeps holding exactly as before.
"""

import copy
import logging
from dataclasses import dataclass
from typing import Any, Callable, Dict, FrozenSet, List, Optional, Tuple

import networkx as nx
from openeo_pg_parser_networkx.graph import OpenEOProcessGraph
from openeo_pg_parser_networkx.process_registry import Process, ProcessRegistry

logger = logging.getLogger(__name__)

#: A ``load_collection`` node's own ``(id, bands)`` signature -- the only
#: thing a rebound callable can observe about which node called it.
SignatureKey = Tuple[str, Tuple[str, ...]]


@dataclass(frozen=True)
class Requirement:
    """A composable statement of what a ``load_collection`` node must
    produce. Deliberately small and generic (ADR 0002 §2.6.1's extensibility
    note), not SAR-shaped -- a future process declares whatever bands it
    needs without this type growing process-specific fields.
    """

    extra_bands: FrozenSet[str] = frozenset()

    def __or__(self, other: "Requirement") -> "Requirement":
        """Union two requirements -- how per-node and per-signature merging compose."""
        return Requirement(extra_bands=self.extra_bands | other.extra_bands)

    def __bool__(self) -> bool:
        """True when this requirement actually asks for something."""
        return bool(self.extra_bands)


_NO_REQUIREMENT = Requirement()

#: Requirement providers, keyed by process id (mirrors
#: ``results_cache._RECOMPUTE_PROCESSES``). Populated via
#: :func:`register_requirement_provider` -- do not write to this dict
#: directly from outside this module.
_REQUIREMENT_PROVIDERS: Dict[
    str, Callable[[Dict[str, Any], Dict[str, Any]], Requirement]
] = {}


def register_requirement_provider(
    process_id: str,
    provider: Callable[[Dict[str, Any], Dict[str, Any]], Requirement],
) -> None:
    """Declare what a process needs from the ``load_collection`` node(s) that
    feed it. ``provider`` is called with that process node's own
    ``resolved_kwargs`` first, then the specific ``load_collection`` node's
    own ``resolved_kwargs`` second (e.g. its ``id``/``bands``) -- the second
    argument exists because a process's requirement can depend on what was
    already requested (e.g. which polarisations), which isn't visible on the
    process's own arguments.

    The extension point this module is designed around (ADR 0002 §2.1):
    a process module registers itself here at import time rather than this
    module knowing about any specific process.
    """
    _REQUIREMENT_PROVIDERS[process_id] = provider


def _signature_key(collection_id: Any, bands: Any) -> Optional[SignatureKey]:
    """Build the dispatch key from an ``id``/``bands`` pair, or ``None`` when
    either isn't an already-resolved plain value -- e.g. a UDP
    ``from_parameter`` reference, still unresolved in a graph node's static
    ``resolved_kwargs`` (resolution happens inside ``load_collection``'s own
    body, at call time, from ``named_parameters``). Such a node is left
    alone rather than guessed at: it degrades to no injection, not a crash
    or a wrong guess.
    """
    if not isinstance(collection_id, str):
        return None
    if bands is not None:
        if not isinstance(bands, list) or not all(isinstance(b, str) for b in bands):
            return None
    return (collection_id, tuple(bands or ()))


def resolve_requirements(graph: OpenEOProcessGraph) -> Dict[SignatureKey, Requirement]:
    """Pre-execution pass (ADR 0002 §2.6.2). For each ``load_collection``
    node, union the requirements of every process in ``nx.ancestors(graph.G,
    node)`` -- its downstream cone -- keyed by the node's own signature
    rather than its identity, per the increment-4 finding in the module
    docstring. Only non-empty requirements are included, so the result is
    empty whenever no process in the graph has a registered provider.
    """
    if not _REQUIREMENT_PROVIDERS:
        return {}

    requirements: Dict[SignatureKey, Requirement] = {}
    for node in graph.G.nodes:
        data = graph.G.nodes[node]
        if data.get("process_id") != "load_collection":
            continue

        resolved_kwargs = data.get("resolved_kwargs") or {}
        key = _signature_key(resolved_kwargs.get("id"), resolved_kwargs.get("bands"))
        if key is None:
            continue

        node_requirement = _NO_REQUIREMENT
        for ancestor in nx.ancestors(graph.G, node):
            provider = _REQUIREMENT_PROVIDERS.get(
                graph.G.nodes[ancestor].get("process_id")
            )
            if provider is None:
                continue
            node_requirement = node_requirement | provider(
                graph.G.nodes[ancestor].get("resolved_kwargs") or {},
                resolved_kwargs,
            )

        if node_requirement:
            requirements[key] = (
                requirements.get(key, _NO_REQUIREMENT) | node_requirement
            )

    return requirements


def _isolated_copy(registry: ProcessRegistry) -> ProcessRegistry:
    """The per-request copy recipe verified in increment 4
    (``tests/test_reader_requirement_channel_spike.py``): ``copy.copy(registry)``
    alone shares ``store`` by reference, so rebinding an entry on "the copy"
    mutates the real, application-lifetime registry too. Copying ``store``
    one namespace-dict level deeper isolates the rebind; untouched ``Process``
    entries stay shared, which is safe because nothing mutates one in place.
    """
    isolated = copy.copy(registry)
    isolated.store = {ns: dict(procs) for ns, procs in registry.store.items()}
    return isolated


def _merge_bands(bands: Optional[List[str]], extra: FrozenSet[str]) -> List[str]:
    """Add required bands the caller didn't already ask for. ``bands=None``
    is treated as "nothing explicit yet" (``[]``), not "all bands" --
    ``LoadCollection.load_collection`` (``stacapi.py``) treats a missing
    ``bands`` as "default to the collection's first asset key", so leaving
    ``None`` untouched would race that default instead of requesting what a
    downstream process actually needs.
    """
    merged = list(bands or [])
    for band in sorted(extra):
        if band not in merged:
            merged.append(band)
    return merged


def build_per_request_registry(
    base_registry: ProcessRegistry,
    requirements: Dict[SignatureKey, Requirement],
) -> ProcessRegistry:
    """The per-request registry channel (ADR 0002 §2.6.3 / ADR 0001 §7.10(b)).

    Returns ``base_registry`` unchanged when there is nothing to inject --
    the strongest form of "a graph with no requiring process must produce a
    byte-identical read" (ADR 0002 §4, increment 5's gate): no copy, no
    wrapping, the exact same object.
    """
    if not any(requirements.values()):
        return base_registry

    original = base_registry["load_collection"].implementation

    def rebound_load_collection(id, bands=None, named_parameters=None, **kwargs):
        key = _signature_key(id, bands)
        requirement = (
            requirements.get(key, _NO_REQUIREMENT)
            if key is not None
            else _NO_REQUIREMENT
        )
        if requirement:
            merged_bands = _merge_bands(bands, requirement.extra_bands)
            logger.info(
                "reader_requirements: load_collection(id=%r) bands %r -> %r "
                "(planner-injected: %s)",
                id,
                bands,
                merged_bands,
                sorted(requirement.extra_bands),
            )
            bands = merged_bands
        return original(id=id, bands=bands, named_parameters=named_parameters, **kwargs)

    per_request = _isolated_copy(base_registry)
    per_request["load_collection"] = Process(
        spec=base_registry["load_collection"].spec,
        implementation=rebound_load_collection,
    )
    return per_request


def plan_process_registry(
    graph: OpenEOProcessGraph, base_registry: ProcessRegistry
) -> ProcessRegistry:
    """Convenience wrapper mirroring ``make_results_cache(graph)``'s
    ergonomics at the call site: resolve requirements from the parsed graph,
    then build the per-request registry that satisfies them.
    """
    return build_per_request_registry(base_registry, resolve_requirements(graph))
