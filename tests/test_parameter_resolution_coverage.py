"""
Additional tests to improve coverage for parameter resolution in stacapi.py
"""

import pytest

from titiler.openeo.stacapi import LoadCollection, stacApiBackend


@pytest.fixture
def load_collection():
    """Create a LoadCollection instance for testing."""
    backend = stacApiBackend(url="https://example.com/stac")
    return LoadCollection(stac_api=backend)


def test_between_operator_parameter_resolution(load_collection):
    """Test between operator with parameter resolution"""
    properties = {
        "cloud_cover": {
            "process_graph": {
                "between1": {
                    "process_id": "between",
                    "arguments": {
                        "x": {"property": "eo:cloud_cover"},
                        "min": {"from_parameter": "min_cloud"},
                        "max": {"from_parameter": "max_cloud"},
                    },
                    "result": True,
                }
            }
        }
    }

    named_parameters = {"min_cloud": 5, "max_cloud": 25}

    result = load_collection._convert_process_graph_to_cql2(
        properties, named_parameters
    )

    assert result["op"] == "between"
    assert result["args"][0] == {"property": "cloud_cover"}
    assert result["args"][1] == 5  # min parameter resolved
    assert result["args"][2] == 25  # max parameter resolved


def test_starts_with_parameter_resolution(load_collection):
    """Test starts_with operator with parameter resolution"""
    properties = {
        "platform": {
            "process_graph": {
                "starts_with1": {
                    "process_id": "starts_with",
                    "arguments": {
                        "data": {"property": "platform"},
                        "pattern": {"from_parameter": "platform_prefix"},
                    },
                    "result": True,
                }
            }
        }
    }

    named_parameters = {"platform_prefix": "sentinel"}

    result = load_collection._convert_process_graph_to_cql2(
        properties, named_parameters
    )

    assert result["op"] == "like"
    assert result["args"][0] == {"property": "platform"}
    assert result["args"][1] == "sentinel%"


def test_ends_with_parameter_resolution(load_collection):
    """Test ends_with operator with parameter resolution"""
    properties = {
        "instrument": {
            "process_graph": {
                "ends_with1": {
                    "process_id": "ends_with",
                    "arguments": {
                        "data": {"property": "instrument"},
                        "pattern": {"from_parameter": "instrument_suffix"},
                    },
                    "result": True,
                }
            }
        }
    }

    named_parameters = {"instrument_suffix": "MSI"}

    result = load_collection._convert_process_graph_to_cql2(
        properties, named_parameters
    )

    assert result["op"] == "like"
    assert result["args"][0] == {"property": "instrument"}
    assert result["args"][1] == "%MSI"


def test_contains_parameter_resolution(load_collection):
    """Test contains operator with parameter resolution"""
    properties = {
        "instrument": {
            "process_graph": {
                "contains1": {
                    "process_id": "contains",
                    "arguments": {
                        "data": {"property": "instrument"},
                        "pattern": {"from_parameter": "instrument_pattern"},
                    },
                    "result": True,
                }
            }
        }
    }

    named_parameters = {"instrument_pattern": "MSI"}

    result = load_collection._convert_process_graph_to_cql2(
        properties, named_parameters
    )

    assert result["op"] == "like"
    assert result["args"][0] == {"property": "instrument"}
    assert result["args"][1] == "%MSI%"


def test_is_null_parameter_resolution(load_collection):
    """Test is_null operator with parameter resolution"""
    properties = {
        "cloud_cover": {
            "process_graph": {
                "is_null1": {
                    "process_id": "is_null",
                    "arguments": {"x": {"property": "eo:cloud_cover"}},
                    "result": True,
                }
            }
        }
    }

    named_parameters = {"some_param": "value"}  # Parameter not used but available

    result = load_collection._convert_process_graph_to_cql2(
        properties, named_parameters
    )

    assert result["op"] == "is null"
    assert result["args"][0] == {"property": "cloud_cover"}


def test_default_operator_parameter_resolution(load_collection):
    """Test default operator handling with parameter resolution"""
    properties = {
        "composite_filter": {
            "process_graph": {
                "and1": {
                    "process_id": "and",  # This will be handled as default operator
                    "arguments": {
                        "x": {
                            "process_id": "lte",
                            "arguments": {
                                "x": {"property": "eo:cloud_cover"},
                                "y": {"from_parameter": "max_cloud"},
                            },
                        },
                        "y": {
                            "process_id": "gte",
                            "arguments": {
                                "x": {"property": "datetime"},
                                "y": {"from_parameter": "min_date"},
                            },
                        },
                    },
                    "result": True,
                }
            }
        }
    }

    named_parameters = {"max_cloud": 30, "min_date": "2023-01-01"}

    result = load_collection._convert_process_graph_to_cql2(
        properties, named_parameters
    )

    # Default operator creates property-based condition
    assert result["op"] == "="
    assert result["args"][0] == {"property": "properties.composite_filter"}
    # The argument should be the whole process graph object
    assert isinstance(result["args"][1], dict)


def test_missing_parameter_handling(load_collection):
    """Test handling when parameter is not provided"""
    properties = {
        "cloud_cover": {
            "process_graph": {
                "lte1": {
                    "process_id": "lte",
                    "arguments": {
                        "x": {"property": "eo:cloud_cover"},
                        "y": {"from_parameter": "missing_param"},
                    },
                    "result": True,
                }
            }
        }
    }

    named_parameters = {}  # Missing parameter

    # Should handle missing parameter gracefully
    result = load_collection._convert_process_graph_to_cql2(
        properties, named_parameters
    )

    # Should still create condition but with original from_parameter reference
    assert result["op"] == "<="
    assert result["args"][0] == {"property": "cloud_cover"}
    assert result["args"][1] == {"from_parameter": "missing_param"}


def test_empty_properties_with_parameters(load_collection):
    """Test empty properties dict with named_parameters"""
    properties = {}
    named_parameters = {"some_param": "some_value"}

    result = load_collection._convert_process_graph_to_cql2(
        properties, named_parameters
    )

    assert result == {}


def test_multiple_properties_with_parameters(load_collection):
    """Test multiple properties with different parameter types"""
    properties = {
        "cloud_cover": {
            "process_graph": {
                "lte1": {
                    "process_id": "lte",
                    "arguments": {
                        "x": {"property": "eo:cloud_cover"},
                        "y": {"from_parameter": "max_cloud"},
                    },
                    "result": True,
                }
            }
        },
        "platform": {
            "process_graph": {
                "eq1": {
                    "process_id": "eq",
                    "arguments": {
                        "x": {"property": "platform"},
                        "y": {"from_parameter": "satellite_name"},
                    },
                    "result": True,
                }
            }
        },
    }

    named_parameters = {"max_cloud": 15, "satellite_name": "sentinel-2"}

    result = load_collection._convert_process_graph_to_cql2(
        properties, named_parameters
    )

    assert result["op"] == "and"
    assert len(result["args"]) == 2

    # Verify both conditions are properly resolved
    conditions = result["args"]
    cloud_condition = next(
        c for c in conditions if c["args"][0]["property"] == "cloud_cover"
    )
    platform_condition = next(
        c for c in conditions if c["args"][0]["property"] == "platform"
    )

    assert cloud_condition["args"][1] == 15
    assert platform_condition["args"][1] == "sentinel-2"


def test_nested_parameter_references(load_collection):
    """Test parameter references in nested structures"""
    properties = {
        "complex_filter": {
            "process_graph": {
                "contains1": {
                    "process_id": "contains",
                    "arguments": {
                        "data": {"property": "platform"},
                        "pattern": {"from_parameter": "platform_name"},
                    },
                    "result": True,
                }
            }
        }
    }

    named_parameters = {"platform_name": "sentinel"}

    result = load_collection._convert_process_graph_to_cql2(
        properties, named_parameters
    )

    assert result["op"] == "like"
    assert result["args"][0] == {"property": "complex_filter"}
    assert result["args"][1] == "%sentinel%"

    # Verify parameter was resolved
    result_str = str(result)
    assert "sentinel" in result_str
    assert "from_parameter" not in result_str
