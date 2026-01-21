"""Test conversion from OpenEO process graphs to CQL2-JSON format.

These tests focus on the CQL2 conversion mechanics - mapping OpenEO process IDs
to CQL2 operators. For parameter resolution tests with ParameterReference objects
from the parser, see test_parameter_resolution.py.
"""

import pytest

from titiler.openeo.stacapi import LoadCollection, stacApiBackend


@pytest.fixture
def load_collection():
    """Create a LoadCollection instance for testing."""
    backend = stacApiBackend(url="https://example.com/stac")
    return LoadCollection(stac_api=backend)


def test_simple_eq_conversion(load_collection):
    """Test conversion of a simple equality process graph."""
    properties = {
        "cloud_cover": {
            "process_graph": {
                "cc": {
                    "process_id": "eq",
                    "arguments": {
                        "x": {"property": "eo:cloud_cover"},
                        "y": 10,
                    },
                    "result": True,
                }
            }
        }
    }

    expected = {"op": "=", "args": [{"property": "cloud_cover"}, 10]}

    result = load_collection._convert_process_graph_to_cql2(properties)
    assert result == expected


def test_between_conversion(load_collection):
    """Test conversion of a between process graph."""
    properties = {
        "cloud_cover": {
            "process_graph": {
                "cc": {
                    "process_id": "between",
                    "arguments": {
                        "x": {"property": "eo:cloud_cover"},
                        "min": 0,
                        "max": 50,
                    },
                    "result": True,
                }
            }
        }
    }

    expected = {
        "op": "between",
        "args": [{"property": "cloud_cover"}, 0, 50],
    }

    result = load_collection._convert_process_graph_to_cql2(properties)
    assert result == expected


def test_multiple_conditions(load_collection):
    """Test combining multiple conditions with AND."""
    properties = {
        "cloud_cover": {
            "process_graph": {
                "cc": {
                    "process_id": "lt",
                    "arguments": {"x": {"property": "eo:cloud_cover"}, "y": 20},
                    "result": True,
                }
            }
        },
        "platform": {
            "process_graph": {
                "pf": {
                    "process_id": "eq",
                    "arguments": {
                        "x": {"property": "platform"},
                        "y": "Sentinel-2B",
                        "case_sensitive": False,
                    },
                    "result": True,
                }
            }
        },
    }

    expected = {
        "op": "and",
        "args": [
            {"op": "<", "args": [{"property": "cloud_cover"}, 20]},
            {"op": "=", "args": [{"property": "platform"}, "Sentinel-2B"]},
        ],
    }

    result = load_collection._convert_process_graph_to_cql2(properties)
    assert result == expected


def test_pattern_matching(load_collection):
    """Test pattern matching operators."""
    properties = {
        "title": {
            "process_graph": {
                "title": {
                    "process_id": "starts_with",
                    "arguments": {"data": {"property": "title"}, "pattern": "Sentinel"},
                    "result": True,
                }
            }
        }
    }

    expected = {"op": "like", "args": [{"property": "title"}, "Sentinel%"]}

    result = load_collection._convert_process_graph_to_cql2(properties)
    assert result == expected


def test_array_operator(load_collection):
    """Test array operators."""
    properties = {
        "band_names": {
            "process_graph": {
                "bands": {
                    "process_id": "in",
                    "arguments": {
                        "x": {"property": "band_names"},
                        "values": ["B02", "B03", "B04"],
                    },
                    "result": True,
                }
            }
        }
    }

    expected = {
        "op": "in",
        "args": [
            {"property": "band_names"},
            {"array": ["B02", "B03", "B04"]},
        ],
    }

    result = load_collection._convert_process_graph_to_cql2(properties)
    assert result == expected


def test_direct_value_conversion(load_collection):
    """Test conversion of a direct value (not a process graph)."""
    properties = {"platform": "Sentinel-2"}

    expected = {"op": "=", "args": [{"property": "platform"}, "Sentinel-2"]}

    result = load_collection._convert_process_graph_to_cql2(properties)
    assert result == expected


def test_empty_properties(load_collection):
    """Test conversion of empty properties."""
    properties = {}
    result = load_collection._convert_process_graph_to_cql2(properties)
    assert result == {}


def test_ends_with_operator(load_collection):
    """Test ends_with pattern operator."""
    properties = {
        "instrument": {
            "process_graph": {
                "ends": {
                    "process_id": "ends_with",
                    "arguments": {
                        "data": {"property": "instrument"},
                        "pattern": "MSI",
                    },
                    "result": True,
                }
            }
        }
    }

    expected = {"op": "like", "args": [{"property": "instrument"}, "%MSI"]}
    result = load_collection._convert_process_graph_to_cql2(properties)
    assert result == expected


def test_contains_operator(load_collection):
    """Test contains pattern operator."""
    properties = {
        "title": {
            "process_graph": {
                "contains": {
                    "process_id": "contains",
                    "arguments": {
                        "data": {"property": "title"},
                        "pattern": "L2A",
                    },
                    "result": True,
                }
            }
        }
    }

    expected = {"op": "like", "args": [{"property": "title"}, "%L2A%"]}
    result = load_collection._convert_process_graph_to_cql2(properties)
    assert result == expected


def test_is_null_operator(load_collection):
    """Test is_null operator."""
    properties = {
        "optional_field": {
            "process_graph": {
                "null_check": {
                    "process_id": "is_null",
                    "arguments": {
                        "x": {"property": "optional_field"},
                    },
                    "result": True,
                }
            }
        }
    }

    expected = {"op": "is null", "args": [{"property": "optional_field"}]}
    result = load_collection._convert_process_graph_to_cql2(properties)
    assert result == expected
