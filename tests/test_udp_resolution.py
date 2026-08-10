"""Unit tests for `titiler.openeo.udp_resolution` — the UDP-inlining graph
transform, exercised directly (no app, no store, no HTTP)."""

from copy import deepcopy

from openeo_pg_parser_networkx import ProcessRegistry
from openeo_pg_parser_networkx.process_registry import Process

from titiler.openeo.udp_resolution import (
    references_only_predefined,
    resolve_udp_references,
    strip_falsy_result_keys,
)


def _registry(*process_ids) -> ProcessRegistry:
    """A registry holding the given predefined processes and nothing else."""
    registry = ProcessRegistry()
    for process_id in process_ids:
        registry[process_id] = Process(spec={"id": process_id}, implementation=None)
    return registry


# A stored UDP as titiler holds it: every node carries an explicit `result`,
# false on the non-output ones.
STORED_UDP = {
    "id": "my_udp",
    "process_graph": {
        "load1": {
            "process_id": "load_collection",
            "arguments": {"id": "S2"},
            "result": False,
        },
        "reduce1": {
            "process_id": "reduce_dimension",
            "arguments": {"data": {"from_node": "load1"}, "dimension": "t"},
            "result": True,
        },
    },
}


def _referencing_graph():
    return {
        "u1": {"process_id": "my_udp", "arguments": {}, "result": False},
        "save1": {
            "process_id": "save_result",
            "arguments": {"data": {"from_node": "u1"}, "format": "png"},
            "result": True,
        },
    }


def test_strip_falsy_result_keys_leaves_only_the_output_node():
    graph = {
        "a": {"process_id": "load_collection", "arguments": {}, "result": False},
        "b": {
            "process_id": "reduce_dimension",
            "arguments": {
                "reducer": {
                    "process_graph": {
                        "c": {"process_id": "first", "arguments": {}, "result": False},
                        "d": {"process_id": "max", "arguments": {}, "result": True},
                    }
                }
            },
            "result": True,
        },
    }
    strip_falsy_result_keys(graph)

    assert "result" not in graph["a"]
    assert graph["b"]["result"] is True
    callback = graph["b"]["arguments"]["reducer"]["process_graph"]
    assert "result" not in callback["c"]
    assert callback["d"]["result"] is True


def test_references_only_predefined_looks_inside_callbacks():
    """A graph whose top-level nodes are all predefined can still reference a
    UDP from inside a callback — the fast path must not call that "nothing to
    resolve"."""
    graph = {
        "reduce1": {
            "process_id": "reduce_dimension",
            "arguments": {
                "reducer": {
                    "process_graph": {
                        "cb1": {"process_id": "my_udp", "arguments": {}, "result": True}
                    }
                }
            },
            "result": True,
        }
    }
    predefined = {"reduce_dimension", "first"}
    assert references_only_predefined(graph, predefined) is False

    graph["reduce1"]["arguments"]["reducer"]["process_graph"]["cb1"]["process_id"] = (
        "first"
    )
    assert references_only_predefined(graph, predefined) is True


def test_resolves_a_udp_referenced_only_from_a_callback():
    """openeo_pg_parser_networkx only descends into a node's `process`
    argument, so a UDP behind a `reducer` needs our own recursion."""
    registry = _registry("load_collection", "reduce_dimension", "first", "save_result")
    callback_udp = {
        "id": "cb_udp",
        "process_graph": {
            "first1": {
                "process_id": "first",
                "arguments": {"data": {"from_parameter": "data"}},
                "result": True,
            }
        },
    }
    graph = {
        "load1": {
            "process_id": "load_collection",
            "arguments": {"id": "S2"},
            "result": False,
        },
        "reduce1": {
            "process_id": "reduce_dimension",
            "arguments": {
                "data": {"from_node": "load1"},
                "dimension": "t",
                "reducer": {
                    "process_graph": {
                        "cb1": {"process_id": "cb_udp", "arguments": {}, "result": True}
                    }
                },
            },
            "result": True,
        },
    }

    resolved = resolve_udp_references(
        graph,
        registry,
        get_udp_spec=lambda process_id, namespace: callback_udp,
        namespace="alice",
    )

    callback = resolved["reduce1"]["arguments"]["reducer"]["process_graph"]
    assert {n["process_id"] for n in callback.values()} == {"first"}


def test_stored_udp_is_not_mutated_by_resolution():
    """A store may hand back a direct reference to its own state
    (`LocalUdpStore` does) — resolving must not edit it."""
    registry = _registry("load_collection", "reduce_dimension", "save_result")
    stored = deepcopy(STORED_UDP)
    snapshot = deepcopy(stored)

    resolve_udp_references(
        _referencing_graph(),
        registry,
        get_udp_spec=lambda process_id, namespace: stored,
        namespace="alice",
    )

    assert stored == snapshot


def test_input_graph_is_not_mutated_by_resolution():
    registry = _registry("load_collection", "reduce_dimension", "save_result")
    graph = _referencing_graph()
    snapshot = deepcopy(graph)

    resolved = resolve_udp_references(
        graph,
        registry,
        get_udp_spec=lambda process_id, namespace: deepcopy(STORED_UDP),
        namespace="alice",
    )

    assert graph == snapshot
    assert resolved is not graph


def test_resolution_does_not_touch_the_shared_registry():
    """The resolver registers each UDP it fetches. Those writes must land on a
    throwaway copy — including when the user_id *is* the registry's own default
    namespace, where a shallow copy would write into the predefined processes.
    """
    registry = _registry("load_collection", "reduce_dimension", "save_result")
    predefined_before = dict(registry[None])

    resolve_udp_references(
        _referencing_graph(),
        registry,
        get_udp_spec=lambda process_id, namespace: deepcopy(STORED_UDP),
        namespace="predefined",
    )

    assert registry[None] == predefined_before
    assert ("predefined", "my_udp") not in registry


def test_unresolvable_reference_returns_the_graph_untouched():
    """An unknown process must fall through to the registry's own error
    downstream, not blow up here."""
    registry = _registry("load_collection", "save_result")
    graph = _referencing_graph()

    resolved = resolve_udp_references(
        graph,
        registry,
        get_udp_spec=lambda process_id, namespace: None,
        namespace="alice",
    )

    assert resolved == graph


def test_graph_of_only_predefined_processes_is_returned_as_is():
    registry = _registry("load_collection", "save_result")
    graph = {
        "load1": {
            "process_id": "load_collection",
            "arguments": {"id": "S2"},
            "result": False,
        },
        "save1": {
            "process_id": "save_result",
            "arguments": {"data": {"from_node": "load1"}, "format": "png"},
            "result": True,
        },
    }

    resolved = resolve_udp_references(
        graph,
        registry,
        get_udp_spec=lambda process_id, namespace: None,
        namespace="alice",
    )

    # Untouched — same object, `result: false` keys still intact.
    assert resolved is graph
    assert resolved["load1"]["result"] is False
