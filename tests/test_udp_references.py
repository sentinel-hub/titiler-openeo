"""Tests for referencing user-defined processes (UDPs) from a process graph.

A graph may "extend another graph" by referencing a stored UDP by its
process_id. The referenced UDP must be inlined (with parameters bound) before
the graph is validated / turned into a service, since the process registry only
holds predefined processes.
"""

import logging


def _store_udp(client, udp_id, process_graph, parameters=None):
    body = {"id": udp_id, "process_graph": process_graph}
    if parameters is not None:
        body["parameters"] = parameters
    resp = client.put(f"/process_graphs/{udp_id}", json=body)
    assert resp.status_code in (200, 201), resp.text
    return resp


# A self-contained UDP (no save_result — the extendable kind).
BASE_UDP = {
    "loadco1": {
        "process_id": "load_collection",
        "arguments": {
            "id": "S2",
            "spatial_extent": {
                "west": 16.1,
                "east": 16.6,
                "north": 48.6,
                "south": 47.2,
            },
            "temporal_extent": ["2017-01-01", "2017-02-01"],
        },
        "result": True,
    },
}


def test_service_create_resolves_udp_reference(app_with_auth):
    """A service whose graph references a stored UDP is accepted, and the
    stored graph is inlined (the UDP process_id is gone, replaced by the
    predefined processes it wraps)."""
    _store_udp(app_with_auth, "base_s2", BASE_UDP)

    service_input = {
        "process": {
            "process_graph": {
                "u1": {"process_id": "base_s2", "arguments": {}},
                "save1": {
                    "process_id": "save_result",
                    "arguments": {"data": {"from_node": "u1"}, "format": "png"},
                    "result": True,
                },
            }
        },
        "type": "xyz",
        "title": "UDP-referencing service",
    }

    create = app_with_auth.post("/services", json=service_input)
    assert create.status_code == 201, create.text

    service_id = create.headers["OpenEO-Identifier"]
    stored = app_with_auth.get(f"/services/{service_id}").json()
    graph = stored["process"]["process_graph"]

    process_ids = {node["process_id"] for node in graph.values()}
    # The UDP reference has been inlined away...
    assert "base_s2" not in process_ids
    # ...into the predefined processes it wraps.
    assert "load_collection" in process_ids
    assert "save_result" in process_ids


# A multi-node UDP whose output is NOT its first node. titiler stores every
# node with an explicit `result` key (false on non-output nodes), so a resolver
# that keys on mere key-presence would wire the reference to `loadco1` instead
# of the real output `reduce1`.
MULTI_NODE_UDP = {
    "loadco1": {
        "process_id": "load_collection",
        "arguments": {
            "id": "S2",
            "spatial_extent": {
                "west": 16.1,
                "east": 16.6,
                "north": 48.6,
                "south": 47.2,
            },
            "temporal_extent": ["2017-01-01", "2017-02-01"],
        },
    },
    "reduce1": {
        "process_id": "reduce_dimension",
        "arguments": {
            "data": {"from_node": "loadco1"},
            "dimension": "t",
            "reducer": {
                "process_graph": {
                    "first1": {
                        "process_id": "first",
                        "arguments": {"data": {"from_parameter": "data"}},
                        "result": True,
                    }
                }
            },
        },
        "result": True,
    },
}


def test_reference_wires_to_udp_output_node(app_with_auth):
    """The external reference to a multi-node UDP resolves to the UDP's *output*
    node, not merely its first node."""
    _store_udp(app_with_auth, "reduce_udp", MULTI_NODE_UDP)

    service_input = {
        "process": {
            "process_graph": {
                "u1": {"process_id": "reduce_udp", "arguments": {}},
                "save1": {
                    "process_id": "save_result",
                    "arguments": {"data": {"from_node": "u1"}, "format": "png"},
                    "result": True,
                },
            }
        },
        "type": "xyz",
        "title": "multi-node UDP",
    }

    create = app_with_auth.post("/services", json=service_input)
    assert create.status_code == 201, create.text

    service_id = create.headers["OpenEO-Identifier"]
    graph = app_with_auth.get(f"/services/{service_id}").json()["process"][
        "process_graph"
    ]
    save = [n for n in graph.values() if n["process_id"] == "save_result"][0]
    source = save["arguments"]["data"]["from_node"]
    # save_result must read from the UDP's output (reduce_dimension), not the
    # first inlined node (load_collection).
    assert graph[source]["process_id"] == "reduce_dimension"


def test_service_create_binds_udp_parameters(app_with_auth):
    """A parameter passed to a referenced UDP is bound into the inlined graph."""
    parameterized = {
        "loadco1": {
            "process_id": "load_collection",
            "arguments": {
                "id": {"from_parameter": "collection"},
                "spatial_extent": {
                    "west": 16.1,
                    "east": 16.6,
                    "north": 48.6,
                    "south": 47.2,
                },
                "temporal_extent": ["2017-01-01", "2017-02-01"],
            },
            "result": True,
        },
    }
    _store_udp(
        app_with_auth,
        "param_load",
        parameterized,
        parameters=[
            {"name": "collection", "schema": {"type": "string"}, "default": "S2"}
        ],
    )

    service_input = {
        "process": {
            "process_graph": {
                "u1": {
                    "process_id": "param_load",
                    "arguments": {"collection": "S2"},
                },
                "save1": {
                    "process_id": "save_result",
                    "arguments": {"data": {"from_node": "u1"}, "format": "png"},
                    "result": True,
                },
            }
        },
        "type": "xyz",
        "title": "parameterized-UDP service",
    }

    create = app_with_auth.post("/services", json=service_input)
    assert create.status_code == 201, create.text

    service_id = create.headers["OpenEO-Identifier"]
    graph = app_with_auth.get(f"/services/{service_id}").json()["process"][
        "process_graph"
    ]
    load_nodes = [n for n in graph.values() if n["process_id"] == "load_collection"]
    assert load_nodes, "no load_collection after inlining"
    # `collection` was bound to the concrete value, not left as a parameter ref.
    assert load_nodes[0]["arguments"]["id"] == "S2"


def test_validation_accepts_udp_reference(app_with_auth):
    """POST /validation reports no errors for a graph that references a UDP."""
    # /validation authenticates via `validate_optional`; the conftest mock only
    # overrides `validate`, so authenticate the optional path too (matching a
    # real signed-in client, which is what lets UDPs resolve).
    from titiler.openeo.auth import User

    app = app_with_auth.app
    app.dependency_overrides[app.endpoints.auth.validate_optional] = lambda: User(
        user_id="test_user"
    )

    _store_udp(app_with_auth, "base_s2_v", BASE_UDP)

    body = {
        "process_graph": {
            "u1": {"process_id": "base_s2_v", "arguments": {}, "result": True},
        }
    }
    resp = app_with_auth.post("/validation", json=body)
    assert resp.status_code == 200, resp.text
    assert resp.json().get("errors", []) == []


def test_unknown_process_still_errors(app_with_auth):
    """A reference to a process that is neither predefined nor a stored UDP
    still fails (resolution must not mask genuine errors)."""
    service_input = {
        "process": {
            "process_graph": {
                "u1": {"process_id": "does_not_exist", "arguments": {}, "result": True},
            }
        },
        "type": "xyz",
        "title": "bad service",
    }
    create = app_with_auth.post("/services", json=service_input)
    assert create.status_code >= 400


# The outer graph that *references* a UDP carries titiler's explicit
# `result: false` on every non-output node too — so a resolver keying on mere
# key-presence picks a source node as the graph's root and strips `result` from
# the real `save_result` output.
def test_outer_graph_keeps_its_own_result_node(app_with_auth):
    """Resolution must not move the outer graph's result node when the
    referencing node isn't the first one in the graph."""
    _store_udp(app_with_auth, "base_s2_outer", BASE_UDP)

    service_input = {
        "process": {
            "process_graph": {
                "load1": {
                    "process_id": "load_collection",
                    "arguments": {
                        "id": "S2",
                        "spatial_extent": {
                            "west": 16.1,
                            "east": 16.6,
                            "north": 48.6,
                            "south": 47.2,
                        },
                        "temporal_extent": ["2017-01-01", "2017-02-01"],
                    },
                },
                "u1": {"process_id": "base_s2_outer", "arguments": {}},
                "merge1": {
                    "process_id": "merge_cubes",
                    "arguments": {
                        "cube1": {"from_node": "load1"},
                        "cube2": {"from_node": "u1"},
                    },
                },
                "save1": {
                    "process_id": "save_result",
                    "arguments": {"data": {"from_node": "merge1"}, "format": "png"},
                    "result": True,
                },
            }
        },
        "type": "xyz",
        "title": "UDP referenced from a non-first node",
    }

    create = app_with_auth.post("/services", json=service_input)
    assert create.status_code == 201, create.text

    service_id = create.headers["OpenEO-Identifier"]
    graph = app_with_auth.get(f"/services/{service_id}").json()["process"][
        "process_graph"
    ]
    results = [n for n in graph.values() if n.get("result")]
    assert len(results) == 1, "exactly one node must be the result node"
    assert results[0]["process_id"] == "save_result"


# A UDP referenced from inside a callback (`reduce_dimension`'s reducer) rather
# than from a top-level node: openeo_pg_parser_networkx only descends into a
# node's `process` argument, so this needs titiler's own callback recursion.
CALLBACK_UDP = {
    "first1": {
        "process_id": "first",
        "arguments": {"data": {"from_parameter": "data"}},
        "result": True,
    },
}


def test_udp_referenced_from_a_callback_is_resolved(app_with_auth):
    """A UDP referenced only from a reducer callback is inlined too."""
    _store_udp(app_with_auth, "cb_first", CALLBACK_UDP)

    service_input = {
        "process": {
            "process_graph": {
                "load1": {
                    "process_id": "load_collection",
                    "arguments": {
                        "id": "S2",
                        "spatial_extent": {
                            "west": 16.1,
                            "east": 16.6,
                            "north": 48.6,
                            "south": 47.2,
                        },
                        "temporal_extent": ["2017-01-01", "2017-02-01"],
                    },
                },
                "reduce1": {
                    "process_id": "reduce_dimension",
                    "arguments": {
                        "data": {"from_node": "load1"},
                        "dimension": "t",
                        "reducer": {
                            "process_graph": {
                                "cb1": {
                                    "process_id": "cb_first",
                                    "arguments": {},
                                    "result": True,
                                }
                            }
                        },
                    },
                },
                "save1": {
                    "process_id": "save_result",
                    "arguments": {"data": {"from_node": "reduce1"}, "format": "png"},
                    "result": True,
                },
            }
        },
        "type": "xyz",
        "title": "UDP referenced from a callback",
    }

    create = app_with_auth.post("/services", json=service_input)
    assert create.status_code == 201, create.text

    service_id = create.headers["OpenEO-Identifier"]
    graph = app_with_auth.get(f"/services/{service_id}").json()["process"][
        "process_graph"
    ]
    reduce_node = [n for n in graph.values() if n["process_id"] == "reduce_dimension"][
        0
    ]
    callback = reduce_node["arguments"]["reducer"]["process_graph"]
    assert {n["process_id"] for n in callback.values()} == {"first"}


def test_udp_store_failure_is_a_503_not_a_bad_graph(app_with_auth, monkeypatch, caplog):
    """A store outage must not be reported as a missing UDP: the caller gets a
    503 rather than a "not found in registry" error blaming their graph."""
    store = app_with_auth.app.endpoints.udp_store

    def boom(*args, **kwargs):
        raise RuntimeError("udp store is down")

    monkeypatch.setattr(type(store), "get_udp", boom)

    service_input = {
        "process": {
            "process_graph": {
                "u1": {"process_id": "some_udp", "arguments": {}, "result": True},
            }
        },
        "type": "xyz",
        "title": "service hitting a broken store",
    }

    with caplog.at_level(logging.WARNING, logger="titiler.openeo.factory"):
        create = app_with_auth.post("/services", json=service_input)

    assert create.status_code == 503
    assert create.json()["code"] == "ServiceUnavailable"
    assert "UDP store lookup failed" in caplog.text


def test_upsert_udp_accepts_a_reference_to_another_udp(app_with_auth):
    """Storing a UDP whose graph references another of the user's stored UDPs
    must succeed -- composing UDPs from other UDPs is explicitly allowed by
    the spec ("a user-defined process can ... be constructed from ... other
    user-defined processes"), not just from predefined ones."""
    _store_udp(app_with_auth, "base_s2", BASE_UDP)

    wrapper = {
        "u1": {
            "process_id": "base_s2",
            "arguments": {},
            "result": True,
        },
    }
    resp = _store_udp(app_with_auth, "wrapper", wrapper)
    assert resp.status_code in (200, 201), resp.text


def test_upsert_udp_stores_the_reference_not_a_resolved_copy(app_with_auth):
    """The stored graph must keep the live reference (`process_id: base_s2`),
    not an inlined/resolved copy -- otherwise editing `base_s2` later would
    never affect `wrapper`, defeating the point of referencing it at all."""
    _store_udp(app_with_auth, "base_s2", BASE_UDP)

    wrapper = {
        "u1": {
            "process_id": "base_s2",
            "arguments": {},
            "result": True,
        },
    }
    _store_udp(app_with_auth, "wrapper", wrapper)

    stored = app_with_auth.get("/process_graphs/wrapper").json()
    process_ids = {n["process_id"] for n in stored["process_graph"].values()}
    assert process_ids == {"base_s2"}


def test_udp_referencing_a_udp_resolves_through_both_levels(app_with_auth):
    """A graph referencing `wrapper` (which itself references `base_s2`) must
    resolve all the way down to predefined processes, not stop after one
    level."""
    _store_udp(app_with_auth, "base_s2", BASE_UDP)
    _store_udp(
        app_with_auth,
        "wrapper",
        {"u1": {"process_id": "base_s2", "arguments": {}, "result": True}},
    )

    service_input = {
        "process": {
            "process_graph": {
                "u1": {"process_id": "wrapper", "arguments": {}},
                "save1": {
                    "process_id": "save_result",
                    "arguments": {"data": {"from_node": "u1"}, "format": "png"},
                    "result": True,
                },
            }
        },
        "type": "xyz",
        "title": "UDP referencing a UDP",
    }
    create = app_with_auth.post("/services", json=service_input)
    assert create.status_code == 201, create.text

    service_id = create.headers["OpenEO-Identifier"]
    graph = app_with_auth.get(f"/services/{service_id}").json()["process"][
        "process_graph"
    ]
    process_ids = {n["process_id"] for n in graph.values()}
    assert "wrapper" not in process_ids
    assert "base_s2" not in process_ids
    assert "load_collection" in process_ids
    assert "save_result" in process_ids


def test_updating_the_referenced_udp_propagates_to_the_wrapper(app_with_auth):
    """Editing `base_s2` after `wrapper` was stored must change what `wrapper`
    resolves to on its *next* use -- the reference stays live, it isn't
    captured once at `wrapper`'s own store time."""
    _store_udp(app_with_auth, "base_s2", BASE_UDP)
    _store_udp(
        app_with_auth,
        "wrapper",
        {"u1": {"process_id": "base_s2", "arguments": {}, "result": True}},
    )

    updated_base = {
        "loadco1": {
            "process_id": "load_collection",
            "arguments": {
                "id": "S2_UPDATED",
                "spatial_extent": {
                    "west": 16.1,
                    "east": 16.6,
                    "north": 48.6,
                    "south": 47.2,
                },
                "temporal_extent": ["2017-01-01", "2017-02-01"],
            },
            "result": True,
        },
    }
    _store_udp(app_with_auth, "base_s2", updated_base)

    service_input = {
        "process": {
            "process_graph": {
                "u1": {"process_id": "wrapper", "arguments": {}},
                "save1": {
                    "process_id": "save_result",
                    "arguments": {"data": {"from_node": "u1"}, "format": "png"},
                    "result": True,
                },
            }
        },
        "type": "xyz",
        "title": "propagation check",
    }
    create = app_with_auth.post("/services", json=service_input)
    assert create.status_code == 201, create.text

    service_id = create.headers["OpenEO-Identifier"]
    graph = app_with_auth.get(f"/services/{service_id}").json()["process"][
        "process_graph"
    ]
    load_node = [n for n in graph.values() if n["process_id"] == "load_collection"][0]
    assert load_node["arguments"]["id"] == "S2_UPDATED"


def test_upsert_udp_still_rejects_a_genuinely_unknown_process(app_with_auth):
    """A process_id that is neither predefined nor one of the user's own UDPs
    must still be rejected at store time, same as before this change."""
    graph = {
        "u1": {
            "process_id": "does_not_exist_anywhere",
            "arguments": {},
            "result": True,
        },
    }
    resp = app_with_auth.put("/process_graphs/bad_wrapper", json={"id": "bad_wrapper", "process_graph": graph})
    assert resp.status_code >= 400


def test_upsert_udp_requires_the_referenced_udps_required_parameters(app_with_auth):
    """A referenced UDP's parameter without a default is required; omitting it
    must fail at store time, same as it would for a predefined process."""
    _store_udp(
        app_with_auth,
        "param_load",
        {
            "loadco1": {
                "process_id": "load_collection",
                "arguments": {
                    "id": {"from_parameter": "collection"},
                    "spatial_extent": {
                        "west": 16.1,
                        "east": 16.6,
                        "north": 48.6,
                        "south": 47.2,
                    },
                    "temporal_extent": ["2017-01-01", "2017-02-01"],
                },
                "result": True,
            },
        },
        parameters=[{"name": "collection", "schema": {"type": "string"}}],
    )

    wrapper = {
        "u1": {"process_id": "param_load", "arguments": {}, "result": True},
    }
    resp = app_with_auth.put(
        "/process_graphs/wrapper_missing_param",
        json={"id": "wrapper_missing_param", "process_graph": wrapper},
    )
    assert resp.status_code >= 400


def test_upsert_udp_surfaces_store_outage_as_503(app_with_auth, monkeypatch, caplog):
    """A store outage hit while checking a referenced process_id must surface
    as a 503, not a misleading 400 blaming the graph."""
    store = app_with_auth.app.endpoints.udp_store

    def boom(*args, **kwargs):
        raise RuntimeError("udp store is down")

    monkeypatch.setattr(type(store), "get_udp", boom)

    graph = {
        "u1": {"process_id": "some_other_udp", "arguments": {}, "result": True},
    }
    with caplog.at_level(logging.WARNING, logger="titiler.openeo.factory"):
        resp = app_with_auth.put(
            "/process_graphs/wrapper_store_down",
            json={"id": "wrapper_store_down", "process_graph": graph},
        )
    assert resp.status_code == 503
