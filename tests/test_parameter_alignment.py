"""Test parameter handling alignment between openeo_result and openeo_xyz_service endpoints."""

import json
from typing import Any

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
