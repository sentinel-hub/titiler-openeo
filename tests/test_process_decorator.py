"""Test process decorator parameter handling."""

from typing import Dict, Optional

import pytest
from openeo_pg_parser_networkx.pg_schema import ParameterReference

from titiler.openeo.auth import User
from titiler.openeo.processes.implementations.core import process


def test_user_parameter_string_type():
    """Test that string-typed parameters get just the user_id."""

    @process
    def example_process(user_id: str) -> Dict:
        """Example process with string user parameter."""
        return {"received_user": user_id}

    # Create a test user
    test_user = User(
        user_id="test123",
        name="Test User",
        roles=["user"],
    )

    # Test with from_parameter
    result = example_process(
        user_id=ParameterReference(from_parameter="_openeo_user"),
        named_parameters={
            "_openeo_user": test_user,
        },
    )

    assert result["received_user"] == "test123"
    assert not isinstance(result["received_user"], User)


def test_user_parameter_object_type():
    """Test that User-typed parameters get the full object."""

    @process
    def example_process(user: User) -> Dict:
        """Example process with User object parameter."""
        return {"received_user": user}

    # Create a test user
    test_user = User(
        user_id="test123",
        name="Test User",
        roles=["user"],
    )

    # Test with from_parameter
    result = example_process(
        user=ParameterReference(from_parameter="_openeo_user"),
        named_parameters={
            "_openeo_user": test_user,
        },
    )

    assert isinstance(result["received_user"], User)
    assert result["received_user"].user_id == "test123"
    assert result["received_user"].name == "Test User"


def test_user_parameter_direct_value():
    """Test that direct values are passed through unchanged."""

    @process
    def example_process(user_id: str) -> Dict:
        """Example process with string user parameter."""
        return {"received_user": user_id}

    # Test with direct value
    result = example_process(user_id="direct123")
    assert result["received_user"] == "direct123"


def test_user_parameter_optional_string():
    """Test that optional string parameters work correctly."""

    @process
    def example_process(user_id: Optional[str] = None) -> Dict:
        """Example process with optional string user parameter."""
        return {"received_user": user_id}

    # Create a test user
    test_user = User(
        user_id="test123",
        name="Test User",
        roles=["user"],
    )

    # Test with from_parameter
    result = example_process(
        user_id=ParameterReference(from_parameter="_openeo_user"),
        named_parameters={
            "_openeo_user": test_user,
        },
    )

    assert result["received_user"] == "test123"

    # Test with no parameter
    result = example_process()
    assert result["received_user"] is None


def test_user_parameter_missing():
    """Test error handling for missing user parameter."""

    @process
    def example_process(user_id: str) -> Dict:
        """Example process with string user parameter."""
        return {"received_user": user_id}

    # Test with missing parameter
    with pytest.raises(Exception) as exc_info:
        example_process(
            user_id=ParameterReference(from_parameter="_openeo_user"),
            named_parameters={},
        )
    assert "missing" in str(exc_info.value).lower()
