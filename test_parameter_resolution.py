#!/usr/bin/env python3
"""Test to verify parameter resolution in properties filtering works correctly."""

import json

from titiler.openeo.stacapi import LoadCollection, stacApiBackend


def test_parameter_resolution():
    """Test that parameter references in properties are properly resolved."""

    # Create a LoadCollection instance
    backend = stacApiBackend(url="https://example.com/stac")
    load_collection = LoadCollection(stac_api=backend)

    # Test properties with parameter reference that should be resolved
    properties = {
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
    }

    # Named parameters that should be used to resolve the reference
    named_parameters = {"cloud_cover": 20}

    # Convert process graph to CQL2 with parameter resolution
    result = load_collection._convert_process_graph_to_cql2(
        properties, named_parameters
    )

    # Expected result after parameter resolution
    expected = {"op": "<=", "args": [{"property": "eo:cloud_cover"}, 20]}

    print("Input properties:", json.dumps(properties, indent=2))
    print("\nNamed parameters:", named_parameters)
    print("\nResult:", json.dumps(result, indent=2))
    print("\nExpected:", json.dumps(expected, indent=2))

    # Verify the result matches expected
    assert result == expected, f"Expected {expected}, but got {result}"
    print("\n✅ Parameter resolution test PASSED!")


if __name__ == "__main__":
    test_parameter_resolution()
