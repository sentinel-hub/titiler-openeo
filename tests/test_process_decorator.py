"""Test process decorator parameter handling."""

from typing import Dict, Optional, Union

import pytest
from openeo_pg_parser_networkx.pg_schema import (
    BoundingBox,
    ParameterReference,
    TemporalInterval,
)

from titiler.openeo.auth import User
from titiler.openeo.processes.implementations.core import (
    _is_optional_type,
    _is_string_type,
    _resolve_special_parameter,
    process,
)


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


def test_is_optional_type():
    """Test _is_optional_type helper function."""
    # Test non-optional types
    is_optional, underlying = _is_optional_type(str)
    assert not is_optional
    assert underlying is str

    is_optional, underlying = _is_optional_type(BoundingBox)
    assert not is_optional
    assert underlying is BoundingBox

    # Test Optional types
    is_optional, underlying = _is_optional_type(Optional[str])
    assert is_optional
    assert underlying is str

    is_optional, underlying = _is_optional_type(Optional[BoundingBox])
    assert is_optional
    assert underlying is BoundingBox

    # Test Union with None (equivalent to Optional)
    is_optional, underlying = _is_optional_type(Union[str, None])
    assert is_optional
    assert underlying is str

    # Test Union with multiple non-None types
    is_optional, underlying = _is_optional_type(Union[str, int, None])
    assert is_optional
    assert underlying == Union[str, int]

    # Test Union without None
    is_optional, underlying = _is_optional_type(Union[str, int])
    assert not is_optional
    assert underlying == Union[str, int]


def test_is_string_type():
    """Test _is_string_type helper function."""
    # Test direct string type
    assert _is_string_type(str)

    # Test Optional[str]
    assert _is_string_type(Optional[str])

    # Test Union[str, None]
    assert _is_string_type(Union[str, None])

    # Test non-string types
    assert not _is_string_type(int)
    assert not _is_string_type(BoundingBox)
    assert not _is_string_type(Optional[int])


def test_resolve_special_parameter_bounding_box():
    """Test _resolve_special_parameter with BoundingBox types."""
    bbox_dict = {
        "west": 10.0,
        "east": 20.0,
        "south": 40.0,
        "north": 50.0,
        "crs": "EPSG:4326",
    }

    # Test with direct BoundingBox type
    result = _resolve_special_parameter("bbox", bbox_dict, BoundingBox)
    assert isinstance(result, BoundingBox)
    assert result.west == 10.0
    assert result.east == 20.0
    assert result.south == 40.0
    assert result.north == 50.0

    # Test with Optional[BoundingBox] type
    result = _resolve_special_parameter("bbox", bbox_dict, Optional[BoundingBox])
    assert isinstance(result, BoundingBox)
    assert result.west == 10.0

    # Test with non-dict value (should return as-is)
    result = _resolve_special_parameter("bbox", "not_a_dict", BoundingBox)
    assert result == "not_a_dict"


def test_resolve_special_parameter_temporal_interval():
    """Test _resolve_special_parameter with TemporalInterval types."""
    # Test with dict input
    temporal_dict = {"start": "2024-01-01T00:00:00Z", "end": "2024-12-31T23:59:59Z"}

    result = _resolve_special_parameter("time", temporal_dict, TemporalInterval)
    assert isinstance(result, TemporalInterval)
    # TemporalInterval has start/end DateTime objects with .root property
    assert result.start.root.year == 2024
    assert result.start.root.month == 1
    assert result.end.root.month == 12

    # Test with Optional[TemporalInterval]
    result = _resolve_special_parameter(
        "time", temporal_dict, Optional[TemporalInterval]
    )
    assert isinstance(result, TemporalInterval)

    # Test with list input
    temporal_list = ["2024-01-01T00:00:00Z", "2024-12-31T23:59:59Z"]
    result = _resolve_special_parameter("time", temporal_list, TemporalInterval)
    assert isinstance(result, TemporalInterval)
    assert result.start.root.year == 2024
    assert result.end.root.month == 12


def test_resolve_special_parameter_user():
    """Test _resolve_special_parameter with user parameter."""
    test_user = User(user_id="test123", name="Test User", roles=["user"])

    # Test with string type (should extract user_id)
    result = _resolve_special_parameter("_openeo_user", test_user, str)
    assert result == "test123"

    # Test with Optional[str] type
    result = _resolve_special_parameter("_openeo_user", test_user, Optional[str])
    assert result == "test123"

    # Test with non-string type (should return as-is)
    result = _resolve_special_parameter("_openeo_user", test_user, User)
    assert result == test_user


def test_process_with_optional_bounding_box():
    """Test process decorator with Optional[BoundingBox] parameter."""

    @process
    def example_process(bbox: Optional[BoundingBox] = None) -> Dict:
        """Example process with optional BoundingBox parameter."""
        if bbox:
            return {"west": bbox.west, "east": bbox.east}
        return {"bbox": None}

    # Test with BoundingBox parameter reference
    bbox_dict = {"west": 10.0, "east": 20.0, "south": 40.0, "north": 50.0}
    result = example_process(
        bbox=ParameterReference(from_parameter="bounding_box"),
        named_parameters={"bounding_box": bbox_dict},
    )

    assert result["west"] == 10.0
    assert result["east"] == 20.0

    # Test with None
    result = example_process()
    assert result["bbox"] is None
