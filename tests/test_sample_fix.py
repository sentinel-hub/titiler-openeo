"""
Unit tests to verify the exact sample.json scenario works.
This recreates the exact error case from the original issue.
"""

import json


def test_original_sample_scenario():
    """Test using the exact sample.json scenario that was failing"""
    from titiler.openeo.stacapi import LoadCollection, stacApiBackend

    # Read the original sample.json
    with open("sample.json", "r") as f:
        sample_data = json.load(f)

    # Extract the properties from the sample that were causing issues
    # Find the load_collection operation in the process_graph
    process_graph = sample_data.get("process_graph", {})
    load_collection_op = None

    for _, node in process_graph.items():
        if node.get("process_id") == "load_collection":
            load_collection_op = node
            break

    assert (
        load_collection_op is not None
    ), "No load_collection operation found in sample"

    properties = load_collection_op.get("arguments", {}).get("properties", {})

    assert "eo:cloud_cover" in properties, "eo:cloud_cover property not found"

    # The parameters that would be passed at runtime
    # Look for the cloud_cover parameter in the parameters section
    parameters_section = sample_data.get("parameters", [])
    cloud_cover_param = None
    for param in parameters_section:
        if param.get("name") == "cloud_cover":
            cloud_cover_param = param.get("default", 20)
            break

    named_parameters = {"cloud_cover": cloud_cover_param or 20}

    # Create LoadCollection with backend
    backend = stacApiBackend(url="https://example.com/stac")
    loader = LoadCollection(stac_api=backend)

    # This was the exact call that was failing before our fix
    result = loader._convert_process_graph_to_cql2(properties, named_parameters)

    # Verify that we got a valid CQL2 result and the parameter was resolved
    assert isinstance(result, dict), "Result should be a dictionary"
    assert "op" in result, "Result should have an 'op' field"

    # Check if the result contains our resolved parameter value (20)
    result_str = json.dumps(result)
    assert "20" in result_str, "Resolved parameter value (20) should be in result"
    assert (
        "from_parameter" not in result_str
    ), "No unresolved parameter references should remain"
