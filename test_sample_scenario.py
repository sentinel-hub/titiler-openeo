#!/usr/bin/env python3
"""Test the exact scenario from sample.json that was causing the error."""

import json

from titiler.openeo.stacapi import LoadCollection, stacApiBackend


def test_sample_json_scenario():
    """Test the exact scenario from the sample.json that was failing."""

    # Create a LoadCollection instance
    backend = stacApiBackend(url="https://example.com/stac")
    load_collection = LoadCollection(stac_api=backend)

    # Properties from the sample.json that was causing issues
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

    # Parameters as they would be resolved from the sample.json
    named_parameters = {
        "time": ["2026-01-10", "2026-01-20"],
        "bounding_box": {"west": -5, "south": 40, "east": 5, "north": 50},
        "bands": ["B04", "B03", "B02"],
        "cloud_cover": 20,  # This parameter should resolve the reference
    }

    print("Testing sample.json scenario...")
    print("Properties:", json.dumps(properties, indent=2))
    print("\nNamed parameters:", json.dumps(named_parameters, indent=2))

    # This should work now with our fix
    try:
        result = load_collection._convert_process_graph_to_cql2(
            properties, named_parameters
        )
        print("\n✅ SUCCESS: Properties converted to CQL2 without error!")
        print("Result:", json.dumps(result, indent=2))

        # Verify the cloud_cover parameter was properly resolved to value 20
        assert result["op"] == "<="
        assert result["args"][0]["property"] == "eo:cloud_cover"
        assert result["args"][1] == 20  # The resolved parameter value

        print("\n✅ Parameter resolution working correctly!")

    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        raise


if __name__ == "__main__":
    test_sample_json_scenario()
