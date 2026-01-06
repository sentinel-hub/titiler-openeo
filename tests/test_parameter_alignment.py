"""Test parameter handling alignment between openeo_result and openeo_xyz_service endpoints."""

import json
from typing import Any

import pyproj
from openeo_pg_parser_networkx.process_registry import Process

from titiler.openeo.models.openapi import ResultRequest
from titiler.openeo.processes.implementations.core import process
from titiler.openeo.processes.implementations.io import SaveResultData


@process
def test_process_with_params(
    param1: str = "default1", param2: int = 42
) -> SaveResultData:
    """A test process that uses multiple parameters."""
    result = {
        "param1": param1,
        "param2": param2,
    }
    return SaveResultData(
        data=json.dumps(result).encode(), media_type="application/json"
    )


def test_openeo_result_basic_validation(app_with_auth):
    """Test basic validation of openeo_result endpoint."""

    # Add test process to registry
    app_with_auth.app.endpoints.process_registry[None]["test_process_with_params"] = (
        Process(
            implementation=test_process_with_params,
            spec={
                "id": "test_process_with_params",
                "description": "A test process with parameters",
                "parameters": [
                    {
                        "name": "param1",
                        "description": "String parameter",
                        "optional": True,
                        "schema": {"type": "string"},
                    },
                    {
                        "name": "param2",
                        "description": "Integer parameter",
                        "optional": True,
                        "schema": {"type": "integer"},
                    },
                ],
            },
        )
    )

    # Simple process graph without parameters
    simple_process_graph = {
        "process_graph": {
            "test1": {
                "process_id": "test_process_with_params",
                "arguments": {
                    "param1": "direct_value",
                    "param2": 42,
                },
                "result": True,
            }
        }
    }

    # Test with basic request
    response = app_with_auth.post(
        "/result",
        json=ResultRequest(process=simple_process_graph).model_dump(exclude_none=True),
    )

    assert response.status_code == 200


def test_openeo_result_parameter_handling(app_with_auth):
    """Test that openeo_result handles parameters correctly."""

    # Add test process to registry
    if (
        "test_process_with_params"
        not in app_with_auth.app.endpoints.process_registry[None]
    ):
        app_with_auth.app.endpoints.process_registry[None][
            "test_process_with_params"
        ] = Process(
            implementation=test_process_with_params,
            spec={
                "id": "test_process_with_params",
                "description": "A test process with parameters",
                "parameters": [
                    {
                        "name": "param1",
                        "description": "String parameter",
                        "optional": True,
                        "schema": {"type": "string"},
                    },
                    {
                        "name": "param2",
                        "description": "Integer parameter",
                        "optional": True,
                        "schema": {"type": "integer"},
                    },
                ],
            },
        )

    # Process graph with parameter references
    process_graph_with_params = {
        "process_graph": {
            "test1": {
                "process_id": "test_process_with_params",
                "arguments": {
                    "param1": {"from_parameter": "str_param"},
                    "param2": {"from_parameter": "int_param"},
                },
                "result": True,
            }
        },
        "parameters": [
            {
                "name": "str_param",
                "description": "String parameter",
                "schema": {"type": "string"},
                "default": "default_value",
            },
            {
                "name": "int_param",
                "description": "Integer parameter",
                "schema": {"type": "integer"},
                "default": 123,
            },
        ],
    }

    # Test with query parameters overriding defaults
    response = app_with_auth.post(
        "/result?str_param=query_value&int_param=456",
        json=ResultRequest(process=process_graph_with_params).model_dump(
            exclude_none=True
        ),
    )

    assert response.status_code == 200
    result = json.loads(response.content)
    assert result["param1"] == "query_value"  # From query parameter
    assert result["param2"] == 456  # From query parameter

    # Test with defaults (no query parameters)
    response2 = app_with_auth.post(
        "/result",
        json=ResultRequest(process=process_graph_with_params).model_dump(
            exclude_none=True
        ),
    )

    assert response2.status_code == 200
    result2 = json.loads(response2.content)
    assert result2["param1"] == "default_value"  # From parameter default
    assert result2["param2"] == 123  # From parameter default


def test_user_parameter_injection(app_with_auth):
    """Test that user parameter is injected correctly."""

    @process
    def test_process_with_user(user: Any = None) -> SaveResultData:
        """A test process that uses user information."""
        result = {
            "user_id": user.user_id if user and hasattr(user, "user_id") else None,
        }
        return SaveResultData(
            data=json.dumps(result).encode(), media_type="application/json"
        )

    # Add test process to registry
    app_with_auth.app.endpoints.process_registry[None]["test_process_with_user"] = (
        Process(
            implementation=test_process_with_user,
            spec={
                "id": "test_process_with_user",
                "description": "A test process that uses user information",
                "parameters": [
                    {
                        "name": "user",
                        "description": "User information",
                        "optional": True,
                        "schema": {"type": "object"},
                    },
                ],
            },
        )
    )

    # Process graph that references user parameter
    process_graph_user = {
        "process_graph": {
            "test1": {
                "process_id": "test_process_with_user",
                "arguments": {
                    "user": {"from_parameter": "_openeo_user"},
                },
                "result": True,
            }
        },
        "parameters": [
            {
                "name": "_openeo_user",
                "description": "OpenEO user",
                "schema": {"type": "object"},
            },
        ],
    }

    # Test openeo_result - user should be automatically injected
    result_response = app_with_auth.post(
        "/result",
        json=ResultRequest(process=process_graph_user).model_dump(exclude_none=True),
    )

    assert result_response.status_code == 200
    result_data = json.loads(result_response.content)
    assert (
        result_data["user_id"] == "test_user"
    )  # User should be injected automatically


def test_complex_parameter_types(app_with_auth):
    """Test handling of complex parameter types like JSON."""

    @process
    def test_process_complex_params(
        array_param: list = None, object_param: dict = None
    ) -> SaveResultData:
        """A test process with complex parameter types."""
        result = {
            "array_param": array_param,
            "object_param": object_param,
        }
        return SaveResultData(
            data=json.dumps(result).encode(), media_type="application/json"
        )

    # Add test process to registry
    app_with_auth.app.endpoints.process_registry[None][
        "test_process_complex_params"
    ] = Process(
        implementation=test_process_complex_params,
        spec={
            "id": "test_process_complex_params",
            "description": "A test process with complex parameters",
            "parameters": [
                {
                    "name": "array_param",
                    "description": "Array parameter",
                    "optional": True,
                    "schema": {"type": "array"},
                },
                {
                    "name": "object_param",
                    "description": "Object parameter",
                    "optional": True,
                    "schema": {"type": "object"},
                },
            ],
        },
    )

    # Process graph with complex parameter types
    process_graph_complex = {
        "process_graph": {
            "test1": {
                "process_id": "test_process_complex_params",
                "arguments": {
                    "array_param": {"from_parameter": "my_array"},
                    "object_param": {"from_parameter": "my_object"},
                },
                "result": True,
            }
        },
        "parameters": [
            {
                "name": "my_array",
                "description": "Array parameter",
                "schema": {"type": "array"},
                "default": [1, 2, 3],
            },
            {
                "name": "my_object",
                "description": "Object parameter",
                "schema": {"type": "object"},
                "default": {"key": "value"},
            },
        ],
    }

    # Test openeo_result with complex JSON parameters in query
    complex_array = json.dumps([4, 5, 6])
    complex_object = json.dumps({"custom": "data"})

    result_response = app_with_auth.post(
        f"/result?my_array={complex_array}&my_object={complex_object}",
        json=ResultRequest(process=process_graph_complex).model_dump(exclude_none=True),
    )

    assert result_response.status_code == 200
    result_data = json.loads(result_response.content)
    assert result_data["array_param"] == [4, 5, 6]  # Should parse JSON from query
    assert result_data["object_param"] == {
        "custom": "data"
    }  # Should parse JSON from query


def test_bounding_box_parameter(app_with_auth):
    """Test that bounding_box parameter works as modern replacement for spatial_extent_*."""
    from openeo_pg_parser_networkx.pg_schema import BoundingBox

    @process
    def test_process_with_bbox(bbox: BoundingBox) -> SaveResultData:
        """Test process that accepts bounding_box parameter."""
        result = {
            "west": bbox.west,
            "east": bbox.east,
            "south": bbox.south,
            "north": bbox.north,
            "crs": bbox.crs,
        }
        return SaveResultData(
            data=json.dumps(result).encode(), media_type="application/json"
        )

    # Add test process to registry
    app_with_auth.app.endpoints.process_registry[None]["test_process_with_bbox"] = (
        Process(
            implementation=test_process_with_bbox,
            spec={
                "id": "test_process_with_bbox",
                "description": "Test process with bounding box parameter",
                "parameters": [
                    {
                        "name": "bbox",
                        "description": "Bounding box parameter",
                        "schema": {"type": "object"},
                        "default": {
                            "west": 10.0,
                            "east": 20.0,
                            "south": 40.0,
                            "north": 50.0,
                            "crs": 4326,
                        },
                    }
                ],
                "returns": {"schema": {"type": "object"}},
            },
        )
    )

    # Test process graph that uses bounding_box parameter
    process_graph_bbox = {
        "process_graph": {
            "test1": {
                "process_id": "test_process_with_bbox",
                "arguments": {"bbox": {"from_parameter": "bounding_box"}},
                "result": True,
            }
        },
        "parameters": [
            {
                "name": "bounding_box",
                "description": "Spatial bounding box",
                "schema": {"type": "object"},
                "default": {
                    "west": 10.0,
                    "east": 20.0,
                    "south": 40.0,
                    "north": 50.0,
                    "crs": 4326,
                },
            }
        ],
    }

    # Test with query parameter override
    bbox_query = json.dumps(
        {"west": 5.0, "east": 15.0, "south": 35.0, "north": 45.0, "crs": 4326}
    )

    result_response = app_with_auth.post(
        f"/result?bounding_box={bbox_query}",
        json=ResultRequest(process=process_graph_bbox).model_dump(exclude_none=True),
    )

    assert result_response.status_code == 200
    result_data = json.loads(result_response.content)

    # Should use query parameter values, not defaults
    assert result_data["west"] == 5.0
    assert result_data["east"] == 15.0
    assert result_data["south"] == 35.0
    assert result_data["north"] == 45.0
    assert result_data["crs"] == pyproj.CRS.from_epsg(4326).to_wkt()

    # Test with default values (no query parameter)
    result_response_default = app_with_auth.post(
        "/result",
        json=ResultRequest(process=process_graph_bbox).model_dump(exclude_none=True),
    )

    assert result_response_default.status_code == 200
    result_data_default = json.loads(result_response_default.content)

    # Should use default values
    assert result_data_default["west"] == 10.0
    assert result_data_default["east"] == 20.0
    assert result_data_default["south"] == 40.0
    assert result_data_default["north"] == 50.0
    assert result_data_default["crs"] == pyproj.CRS.from_epsg(4326).to_wkt()
