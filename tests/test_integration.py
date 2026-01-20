"""
Integration tests to verify the complete parameter resolution solution.
"""

import json


def test_full_cql2_conversion():
    """Test complete CQL2 conversion with parameter resolution"""
    from titiler.openeo.stacapi import LoadCollection, stacApiBackend

    # Read the sample process graph
    with open("sample.json", "r") as f:
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

    # Parameters with actual values that should replace the references
    named_parameters = {"cloud_cover": 20}

    # Create LoadCollection instance
    backend = stacApiBackend(url="https://example.com/stac")
    loader = LoadCollection(stac_api=backend)

    # Test CQL2 conversion
    result = loader._convert_process_graph_to_cql2(properties, named_parameters)

    # Verify the result structure
    expected = {"op": "<=", "args": [{"property": "eo:cloud_cover"}, 20]}

    assert result == expected


def test_parameter_resolution_edge_cases():
    """Test parameter resolution with edge cases"""
    from titiler.openeo.stacapi import LoadCollection, stacApiBackend

    # Test with multiple parameters
    properties = {
        "cloud_cover": {
            "process_graph": {
                "cc": {
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

    named_parameters = {"min_cloud": 0, "max_cloud": 30}

    backend = stacApiBackend(url="https://example.com/stac")
    loader = LoadCollection(stac_api=backend)

    result = loader._convert_process_graph_to_cql2(properties, named_parameters)

    assert result["op"] == "between"
    assert result["args"][0] == {"property": "cloud_cover"}
    assert result["args"][1] == 0
    assert result["args"][2] == 30
