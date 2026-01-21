"""
Comprehensive tests for parameter resolution in LoadCollection.

Tests the complete parameter resolution flow:
1. ParameterReference objects (production path)
2. End-to-end integration with OpenEOProcessGraph parser
3. Edge cases and complex nested graphs
4. Backwards compatibility with dict format (one test only)

Consolidates tests from:
- test_core_fix.py (core functionality)
- test_integration.py (sample.json integration)
- test_issue_186_integration.py (ParameterReference handling)
"""

import json

import pytest
from openeo_pg_parser_networkx import OpenEOProcessGraph
from openeo_pg_parser_networkx.pg_schema import ParameterReference

from titiler.openeo.stacapi import LoadCollection, stacApiBackend


@pytest.fixture
def load_collection():
    """Create a LoadCollection instance for testing."""
    backend = stacApiBackend(url="https://example.com/stac")
    return LoadCollection(stac_api=backend)


# =============================================================================
# Core Parameter Resolution Tests - Production Path with ParameterReference
# =============================================================================


def test_core_parameter_resolution(load_collection):
    """Test core parameter resolution with ParameterReference objects."""
    properties = {
        "cloud_cover": {
            "process_graph": {
                "cc": {
                    "process_id": "lte",
                    "arguments": {
                        "x": {"property": "eo:cloud_cover"},
                        "y": ParameterReference(from_parameter="cloud_cover"),
                    },
                    "result": True,
                }
            }
        }
    }

    named_parameters = {"cloud_cover": 20}
    result = load_collection._convert_process_graph_to_cql2(
        properties, named_parameters
    )

    expected = {"op": "<=", "args": [{"property": "cloud_cover"}, 20]}
    assert result == expected


def test_complex_nested_graph(load_collection):
    """Test with a complex nested process graph with multiple ParameterReference objects."""
    properties = {
        "cloud_cover": {
            "process_graph": {
                "cc": {
                    "process_id": "lte",
                    "arguments": {
                        "x": {"property": "eo:cloud_cover"},
                        "y": ParameterReference(from_parameter="max_cloud_cover"),
                    },
                    "result": True,
                }
            }
        },
        "platform": {
            "process_graph": {
                "platform_filter": {
                    "process_id": "eq",
                    "arguments": {
                        "x": {"property": "platform"},
                        "y": ParameterReference(from_parameter="satellite"),
                    },
                    "result": True,
                }
            }
        },
    }

    named_parameters = {"max_cloud_cover": 30, "satellite": "sentinel-2"}
    result = load_collection._convert_process_graph_to_cql2(
        properties, named_parameters
    )

    # Check that this is an AND condition with two sub-conditions
    assert result.get("op") == "and"
    assert "args" in result
    assert len(result["args"]) == 2

    # Check first condition (cloud cover)
    first_condition = result["args"][0]
    assert first_condition.get("op") == "<="
    assert first_condition["args"][0] == {"property": "cloud_cover"}
    assert first_condition["args"][1] == 30

    # Check second condition (platform)
    second_condition = result["args"][1]
    assert second_condition.get("op") == "="
    assert second_condition["args"][0] == {"property": "platform"}
    assert second_condition["args"][1] == "sentinel-2"


# =============================================================================
# Integration Tests with sample.json (from test_integration.py)
# =============================================================================


def test_full_cql2_conversion_with_sample_file(load_collection):
    """Test complete CQL2 conversion using the sample.json process graph."""
    with open("tests/sample.json", "r") as f:
        sample_data = json.load(f)

    # Extract the load_collection operation
    process_graph = sample_data.get("process_graph", {})
    load_collection_op = None
    for _, node in process_graph.items():
        if node.get("process_id") == "load_collection":
            load_collection_op = node
            break

    assert load_collection_op is not None

    properties = load_collection_op.get("arguments", {}).get("properties", {})
    named_parameters = {"cloud_cover": 20}

    result = load_collection._convert_process_graph_to_cql2(
        properties, named_parameters
    )

    expected = {"op": "<=", "args": [{"property": "eo:cloud_cover"}, 20]}
    assert result == expected


def test_parameter_resolution_edge_cases(load_collection):
    """Test parameter resolution with between operator and multiple ParameterReference objects."""
    properties = {
        "cloud_cover": {
            "process_graph": {
                "cc": {
                    "process_id": "between",
                    "arguments": {
                        "x": {"property": "eo:cloud_cover"},
                        "min": ParameterReference(from_parameter="min_cloud"),
                        "max": ParameterReference(from_parameter="max_cloud"),
                    },
                    "result": True,
                }
            }
        }
    }

    named_parameters = {"min_cloud": 0, "max_cloud": 30}
    result = load_collection._convert_process_graph_to_cql2(
        properties, named_parameters
    )

    assert result["op"] == "between"
    assert result["args"][0] == {"property": "cloud_cover"}
    assert result["args"][1] == 0
    assert result["args"][2] == 30


# =============================================================================
# ParameterReference Tests (from test_issue_186_integration.py)
# =============================================================================


def test_end_to_end_with_parser():
    """Test complete flow from OpenEOProcessGraph parser to CQL2 conversion.

    This is the production path:
    1. Parser converts {"from_parameter": "x"} to ParameterReference objects
    2. LoadCollection resolves ParameterReference objects using named_parameters
    3. CQL2 filter is generated with resolved values
    """
    process_graph_json = {
        "parameters": [
            {
                "name": "cloud_cover",
                "description": "Maximum cloud cover",
                "schema": {"type": "number"},
                "default": 20,
                "optional": True,
            }
        ],
        "process_graph": {
            "loadcollection1": {
                "process_id": "load_collection",
                "arguments": {
                    "id": "sentinel-2-l2a",
                    "properties": {
                        "eo:cloud_cover": {
                            "process_graph": {
                                "lte1": {
                                    "process_id": "lte",
                                    "arguments": {
                                        "x": {"from_parameter": "value"},
                                        "y": {"from_parameter": "cloud_cover"},
                                    },
                                    "result": True,
                                }
                            }
                        }
                    },
                },
                "result": True,
            }
        },
    }

    # Parse with actual parser - converts to ParameterReference objects
    pg = OpenEOProcessGraph(pg_data=process_graph_json)

    # Get the load_collection node
    load_collection_node = None
    for _, node_data in pg.G.nodes(data=True):
        if node_data.get("process_id") == "load_collection":
            load_collection_node = node_data
            break

    assert load_collection_node is not None

    # Get properties from resolved_kwargs
    resolved_kwargs = load_collection_node.get("resolved_kwargs", {})
    properties = resolved_kwargs.get("properties")

    assert properties is not None
    assert "eo:cloud_cover" in properties

    # Verify parser created ParameterReference objects (not dicts)
    pg_dict = properties["eo:cloud_cover"]["process_graph"]
    lte_node = pg_dict["lte1"]
    y_arg = lte_node["arguments"]["y"]

    assert isinstance(y_arg, ParameterReference)
    assert y_arg.from_parameter == "cloud_cover"

    # Test LoadCollection handles ParameterReference objects
    backend = stacApiBackend(url="https://example.com/stac")
    loader = LoadCollection(stac_api=backend)
    named_parameters = {"cloud_cover": 15}

    result = loader._convert_process_graph_to_cql2(properties, named_parameters)

    # Verify ParameterReference was resolved correctly
    assert result["op"] == "<="
    assert result["args"][0] == {"property": "eo:cloud_cover"}
    assert result["args"][1] == 15


def test_parser_creates_parameter_references():
    """Verify OpenEOProcessGraph parser creates ParameterReference objects, not dicts."""
    process_graph_json = {
        "process_graph": {
            "node1": {
                "process_id": "load_collection",
                "arguments": {
                    "id": {"from_parameter": "collection_id"},
                    "properties": {
                        "cloud_cover": {
                            "process_graph": {
                                "lte1": {
                                    "process_id": "lte",
                                    "arguments": {
                                        "x": {"from_parameter": "value"},
                                        "y": {"from_parameter": "max_cloud"},
                                    },
                                    "result": True,
                                }
                            }
                        }
                    },
                },
                "result": True,
            }
        }
    }

    pg = OpenEOProcessGraph(pg_data=process_graph_json)

    for _, node_data in pg.G.nodes(data=True):
        if node_data.get("process_id") == "load_collection":
            resolved_kwargs = node_data.get("resolved_kwargs", {})

            # Check that 'id' is a ParameterReference
            collection_id = resolved_kwargs.get("id")
            assert isinstance(collection_id, ParameterReference)
            assert collection_id.from_parameter == "collection_id"

            # Check nested ParameterReferences in properties
            properties = resolved_kwargs.get("properties", {})
            pg_dict = properties["cloud_cover"]["process_graph"]
            lte_node = pg_dict["lte1"]

            x_arg = lte_node["arguments"]["x"]
            y_arg = lte_node["arguments"]["y"]

            assert isinstance(x_arg, ParameterReference)
            assert isinstance(y_arg, ParameterReference)
            assert x_arg.from_parameter == "value"
            assert y_arg.from_parameter == "max_cloud"
            return

    pytest.fail("load_collection node not found")


def test_helper_function_handles_both_types(load_collection):
    """Test _resolve_parameter_reference handles both ParameterReference and dict."""
    named_parameters = {"param1": 100, "param2": "value2"}

    # Test with ParameterReference object (production path)
    param_ref = ParameterReference(from_parameter="param1")
    is_ref, resolved = load_collection._resolve_parameter_reference(
        param_ref, named_parameters
    )
    assert is_ref is True
    assert resolved == 100

    # Test with dict (backwards compatibility)
    param_dict = {"from_parameter": "param2"}
    is_ref, resolved = load_collection._resolve_parameter_reference(
        param_dict, named_parameters
    )
    assert is_ref is True
    assert resolved == "value2"

    # Test with non-reference value
    normal_value = "just_a_string"
    is_ref, resolved = load_collection._resolve_parameter_reference(
        normal_value, named_parameters
    )
    assert is_ref is False
    assert resolved is None

    # Test with missing ParameterReference - returns None
    missing_ref = ParameterReference(from_parameter="missing")
    is_ref, resolved = load_collection._resolve_parameter_reference(
        missing_ref, named_parameters
    )
    assert is_ref is True
    assert resolved is None

    # Test with missing dict reference - keeps original for backwards compat
    missing_dict = {"from_parameter": "missing"}
    is_ref, resolved = load_collection._resolve_parameter_reference(
        missing_dict, named_parameters
    )
    assert is_ref is True
    assert resolved == missing_dict
