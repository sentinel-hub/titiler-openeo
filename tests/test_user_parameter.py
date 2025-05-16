"""Test user parameter passing in process nodes."""

from typing import Any, Dict

from fastapi import HTTPException
from openeo_pg_parser_networkx.process_registry import Process

from titiler.openeo.models import ResultRequest
from titiler.openeo.processes.implementations.core import process
from titiler.openeo.processes.implementations.io import SaveResultData


def test_user_parameter_in_process(app_with_auth):
    """Test that user parameter is correctly passed to process nodes."""

    @process
    def process_with_user(user: Any = None) -> SaveResultData:
        """A test process that requires user information."""
        if not user:
            raise HTTPException(status_code=400, detail="User not provided")
        if user.user_id != "test_user":
            raise HTTPException(status_code=400, detail="Unexpected user")
        return SaveResultData(data=b"test", media_type="text/plain")

    # Add our test process to the existing registry
    app_with_auth.app.endpoints.process_registry[None]["test_user_process"] = Process(
        implementation=process_with_user,
        spec={
            "id": "test_user_process",
            "description": "A test process that requires user information",
            "parameters": [
                {
                    "name": "user",
                    "description": "User information",
                    "optional": True,
                    "schema": {"type": "object"},
                }
            ],
        },
    )

    # Create a process graph that uses our test process
    process_graph: Dict[str, Any] = {
        "process_graph": {
            "test1": {
                "process_id": "test_user_process",
                "arguments": {"user": {"from_parameter": "user"}},
                "result": True,
            }
        }
    }

    # Make request to /result endpoint
    response = app_with_auth.post(
        "/result",
        json=ResultRequest(process=process_graph).model_dump(exclude_none=True),
    )

    assert response.status_code == 200
    assert response.content == b"test"
