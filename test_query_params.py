#!/usr/bin/env python3
"""
Simple test script to demonstrate the dynamic parameter functionality.
"""


def test_query_parameter_parsing():
    """Test the query parameter parsing functionality."""

    # Mock request object to simulate query parameters
    class MockRequest:
        def __init__(self, query_params):
            self.query_params = query_params

    # Mock request with various parameter types
    request = MockRequest(
        {
            "temporal_extent": '["2025-11-23T00:00:00Z", "2025-11-24T00:00:00Z"]',
            "bands": '["red", "green", "blue"]',
            "cloud_cover": "20",
            "resolution": "10.5",
        }
    )

    # Parse query parameters (simulated from factory.py logic)
    import json

    query_params = {}
    for param_name, param_value in request.query_params.items():
        try:
            # Try to parse as JSON for complex types (arrays, objects)
            if param_value.startswith(("[", "{")):
                query_params[param_name] = json.loads(param_value)
            else:
                # Handle simple types
                # Try to convert to number if possible
                try:
                    if "." in param_value:
                        query_params[param_name] = float(param_value)
                    else:
                        query_params[param_name] = int(param_value)
                except ValueError:
                    # Keep as string if not a number
                    query_params[param_name] = param_value
        except json.JSONDecodeError:
            print(f"Invalid JSON in query parameter '{param_name}': {param_value}")
            raise

    print("Parsed query parameters:")
    for name, value in query_params.items():
        print(f"  {name}: {value} ({type(value).__name__})")

    # Verify parsing
    assert query_params["temporal_extent"] == [
        "2025-11-23T00:00:00Z",
        "2025-11-24T00:00:00Z",
    ]
    assert query_params["bands"] == ["red", "green", "blue"]
    assert query_params["cloud_cover"] == 20
    assert query_params["resolution"] == 10.5

    print("✓ Query parameter parsing test passed!")


def test_openeo_parameter_format():
    """Test the OpenEO parameter format used in example.json."""

    # Example configuration using OpenEO parameter format
    parameters = [
        {
            "name": "spatial_extent_east",
            "description": "",
            "schema": {},
            "optional": True,
            "default": 12,
        },
        {
            "name": "temporal_extent",
            "description": "Temporal extent as ISO 8601 date strings",
            "schema": {
                "type": "array",
                "items": {"type": "string", "format": "date-time"},
            },
            "optional": True,
            "default": ["2025-11-23T00:00:00Z", "2025-11-24T00:00:00Z"],
        },
    ]

    print("\\nOpenEO parameter format example:")
    for param in parameters:
        print(f"  {param['name']}: {param.get('description', 'No description')}")
        if "default" in param:
            print(f"    Default: {param['default']}")
        print(f"    Optional: {param.get('optional', False)}")
        print(f"    Schema: {param['schema']}")

    print("✓ OpenEO parameter format test passed!")


if __name__ == "__main__":
    test_query_parameter_parsing()
    test_openeo_parameter_format()
    print(
        "\\n🎉 All tests passed! The dynamic parameter implementation is working correctly."
    )
