"""
Unit tests to verify the core parameter resolution fix works correctly.
This tests just the stacapi functionality without requiring app initialization.
"""


def test_core_parameter_resolution():
    """Test core parameter resolution functionality"""
    from titiler.openeo.stacapi import LoadCollection, stacApiBackend

    # Properties dict with parameter reference (format that the method expects)
    properties = {
        "cloud_cover": {
            "process_graph": {
                "cc": {
                    "process_id": "lte",
                    "arguments": {
                        "x": {"property": "eo:cloud_cover"},
                        "y": {"from_parameter": "cloud_cover"},
                    },
                    "result": True,
                }
            }
        }
    }

    # Named parameters that should resolve the reference
    named_parameters = {"cloud_cover": 20}

    # Create a LoadCollection instance with mock backend
    backend = stacApiBackend(url="https://example.com/stac")
    loader = LoadCollection(stac_api=backend)

    # Test the CQL2 conversion with parameter resolution
    result = loader._convert_process_graph_to_cql2(properties, named_parameters)

    # Verify the parameter was resolved correctly
    expected = {"op": "<=", "args": [{"property": "cloud_cover"}, 20]}

    assert result == expected


def test_complex_nested_graph():
    """Test with a more complex nested process graph"""
    from titiler.openeo.stacapi import LoadCollection, stacApiBackend

    properties = {
        "cloud_cover": {
            "process_graph": {
                "cc": {
                    "process_id": "lte",
                    "arguments": {
                        "x": {"property": "eo:cloud_cover"},
                        "y": {"from_parameter": "max_cloud_cover"},
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
                        "y": {"from_parameter": "satellite"},
                    },
                    "result": True,
                }
            }
        },
    }

    named_parameters = {"max_cloud_cover": 30, "satellite": "sentinel-2"}

    backend = stacApiBackend(url="https://example.com/stac")
    loader = LoadCollection(stac_api=backend)
    result = loader._convert_process_graph_to_cql2(properties, named_parameters)

    # Check that this is an AND condition with two sub-conditions
    assert result.get("op") == "and"
    assert "args" in result
    assert len(result["args"]) == 2

    # Check first condition (cloud cover)
    first_condition = result["args"][0]
    assert first_condition.get("op") == "<="
    assert len(first_condition.get("args", [])) == 2
    assert first_condition["args"][0] == {"property": "cloud_cover"}
    assert first_condition["args"][1] == 30

    # Check second condition (platform)
    second_condition = result["args"][1]
    assert second_condition.get("op") == "="
    assert len(second_condition.get("args", [])) == 2
    assert second_condition["args"][0] == {"property": "platform"}
    assert second_condition["args"][1] == "sentinel-2"
