"""Inlining of user-defined process (UDP) references in a flat process graph.

A process graph may reference a stored UDP by ``process_id`` -- a "graph that
extends another graph". The process registry only holds predefined processes,
so such a reference must be inlined (with its parameters bound) before the
graph is parsed or executed. The actual inlining is delegated to
openeo_pg_parser_networkx's :func:`resolve_process_graph`; this module owns the
two adaptations titiler needs on top of it:

* **Result-key normalisation** (:func:`strip_falsy_result_keys`). The resolver's
  ``_get_result_node`` treats the *first* node that merely *has* a ``result``
  key as a graph's output, true or false. titiler's ``ProcessGraph`` model
  defaults ``result`` to ``False`` rather than ``None``, so every node carries
  an explicit ``result: false`` through ``model_dump()`` and the resolver would
  pick a source node as the root -- wiring the reference to the wrong node, or
  failing outright with "Found no result node in flat process graph" once the
  real output's ``result`` key has been stripped as "non-root". Dropping falsy
  ``result`` keys first leaves exactly the true output for it to find.
* **Callback recursion** (:func:`_resolve`). The resolver only descends into a
  node's ``process`` argument, so a UDP referenced from any other callback
  (``reduce_dimension``'s ``reducer``, ``aggregate_temporal``'s ``reducer``,
  ...) would be left unresolved. We recurse into every argument carrying a
  nested ``process_graph`` instead, including those inside a UDP body that only
  appeared after inlining.

This is a pure process-graph transform: it takes a registry and a UDP-lookup
callable rather than reaching for request state, which keeps it out of
``factory.py``'s routing layer and unit-testable on its own
(``tests/test_udp_resolution.py``).
"""

import logging
from copy import deepcopy
from typing import Any, Callable, Dict, Iterator, Optional, Set

from openeo_pg_parser_networkx import ProcessRegistry
from openeo_pg_parser_networkx.resolving_utils import resolve_process_graph

from .errors import ServiceUnavailable
from .reader_requirements import _isolated_copy

logger = logging.getLogger(__name__)

#: ``(process_id, namespace) -> UDP spec``, as :func:`resolve_process_graph`
#: calls it. Returning ``None`` means "no such UDP", which the resolver turns
#: into a resolution failure.
GetUdpSpec = Callable[[str, str], Optional[dict]]


def _callback_arguments(node: Any) -> Iterator[dict]:
    """Yield a node's arguments that carry a nested process graph (a callback)."""
    arguments = node.get("arguments") if isinstance(node, dict) else None
    if not isinstance(arguments, dict):
        return
    for argument in arguments.values():
        if isinstance(argument, dict) and isinstance(
            argument.get("process_graph"), dict
        ):
            yield argument


def strip_falsy_result_keys(process_graph: Any) -> None:
    """Drop ``result`` keys that aren't ``True`` from every node of a (possibly
    nested) process graph, in place.

    See the module docstring: the resolver keys on the mere *presence* of a
    ``result`` key, so titiler's explicit ``result: false`` on non-output nodes
    would misdirect it to the wrong node.
    """
    if not isinstance(process_graph, dict):
        return
    for node in process_graph.values():
        if not isinstance(node, dict):
            continue
        if node.get("result") is not True:
            node.pop("result", None)
        for argument in _callback_arguments(node):
            strip_falsy_result_keys(argument["process_graph"])


def references_only_predefined(process_graph: Any, predefined: Set[str]) -> bool:
    """Whether every process referenced by the graph -- at any callback depth --
    is a predefined one, i.e. there is nothing for resolution to inline.

    Checking callbacks too (and not just the top-level nodes) is what keeps this
    fast path's notion of "nothing to resolve" in step with the resolver's: a
    graph whose top-level nodes are all predefined can still reference a UDP
    from inside a ``reducer``.
    """
    if not isinstance(process_graph, dict):
        return True
    for node in process_graph.values():
        if not isinstance(node, dict):
            continue
        process_id = node.get("process_id")
        if process_id is not None and process_id not in predefined:
            return False
        for argument in _callback_arguments(node):
            if not references_only_predefined(argument["process_graph"], predefined):
                return False
    return True


def _resolve(
    process_graph: Dict[str, Any],
    registry: ProcessRegistry,
    get_udp_spec: GetUdpSpec,
    namespace: str,
    predefined: Set[str],
) -> Dict[str, Any]:
    """Resolve one graph level, then every callback it (still) contains."""
    resolved = resolve_process_graph(
        process_graph,
        registry,
        get_udp_spec=get_udp_spec,
        namespace=namespace,
    )
    for node in resolved.values():
        for argument in _callback_arguments(node):
            callback = argument["process_graph"]
            # Callbacks the resolver already handled itself (``process``
            # arguments) come back fully predefined and are skipped here.
            if references_only_predefined(callback, predefined):
                continue
            argument["process_graph"] = _resolve(
                callback, registry, get_udp_spec, namespace, predefined
            )
    return resolved


def resolve_udp_references(
    process_graph: Dict[str, Any],
    process_registry: ProcessRegistry,
    get_udp_spec: GetUdpSpec,
    namespace: str,
) -> Dict[str, Any]:
    """Inline every UDP referenced by ``process_graph`` and return the result.

    The input graph is never mutated, and neither is anything ``get_udp_spec``
    hands back -- a store may return a direct reference to its own state.

    Returns the graph unchanged when it references only predefined processes, or
    on any resolution failure, so a genuinely unknown process still surfaces the
    normal "not found in registry" error downstream instead of a 500. A store
    outage is not a resolution failure and propagates instead -- treating it as
    one would report an infrastructure problem as a bad graph.
    """
    if not process_graph:
        return process_graph

    predefined = set(process_registry[None].keys())
    if references_only_predefined(process_graph, predefined):
        return process_graph

    def fetch_udp_spec(process_id: str, udp_namespace: str) -> Optional[dict]:
        udp = get_udp_spec(process_id, udp_namespace)
        if not isinstance(udp, dict):
            return udp
        # Copy before normalising: a store may hand back its own internal dict
        # (``LocalUdpStore`` does), and reading a UDP must not edit what's stored.
        udp = deepcopy(udp)
        strip_falsy_result_keys(udp.get("process_graph"))
        return udp

    # Resolve against a throwaway registry so the resolver's per-user UDP writes
    # stay off the shared, application-lifetime one -- avoiding cross-request
    # leakage and stale-UDP caching (the resolver only re-fetches a UDP it hasn't
    # already cached). The copy must isolate the per-namespace dicts, not just
    # the outer one: a user_id of ``predefined`` (the registry's own default
    # namespace) would otherwise write straight into the real predefined
    # processes -- the same hazard ``_isolated_copy`` was written for.
    scratch = _isolated_copy(process_registry)

    graph = deepcopy(process_graph)
    strip_falsy_result_keys(graph)
    try:
        return _resolve(graph, scratch, fetch_udp_spec, namespace, predefined)
    except Exception as exc:  # noqa: BLE001
        # The resolver rewraps whatever ``fetch_udp_spec`` raises into a generic
        # ValueError, chaining the original as ``__cause__``, so a store outage
        # is only distinguishable by walking that chain.
        store_failure = _store_failure_in(exc)
        if store_failure is not None:
            # No `from exc`: `store_failure` already carries the original store
            # error as its cause, and chaining it to its own wrapper would make
            # the cause chain circular.
            raise store_failure  # noqa: B904
        logger.warning(
            "Could not resolve user-defined process references for namespace %r; "
            "leaving the graph unresolved.",
            namespace,
            exc_info=True,
        )
        return process_graph


def _store_failure_in(exc: BaseException) -> Optional[ServiceUnavailable]:
    """Return the ``ServiceUnavailable`` in ``exc``'s cause chain, if any."""
    seen: Set[int] = set()
    current: Optional[BaseException] = exc
    while current is not None and id(current) not in seen:
        if isinstance(current, ServiceUnavailable):
            return current
        seen.add(id(current))
        current = current.__cause__
    return None
