"""Test tile assignment user parameter handling."""

from typing import Dict, Optional, Tuple

import pytest
from openeo_pg_parser_networkx.pg_schema import ParameterReference

from titiler.openeo.auth import User
from titiler.openeo.processes.implementations.tile_assignment import tile_assignment
from titiler.openeo.services.base import TileAssignmentStore


class MockTileStore(TileAssignmentStore):
    """Mock tile store for testing."""

    def __init__(self):
        """Initialize mock tile store."""
        self.tiles = {}
        self.last_user_id = None

    def __repr__(self) -> str:
        """Return string representation of mock store."""
        return f"MockTileStore(tiles={self.tiles}, last_user_id={self.last_user_id})"

    def claim_tile(
        self,
        service_id: str,
        user_id: str,
        zoom: int,
        x_range: Tuple[int, int],
        y_range: Tuple[int, int],
    ) -> Dict:
        """Mock claiming a tile."""
        self.last_user_id = user_id
        return {"x": x_range[0], "y": y_range[0], "z": zoom, "stage": "claimed"}

    def get_user_tile(self, service_id: str, user_id: str) -> Optional[Dict]:
        """Mock getting user's tile."""
        self.last_user_id = user_id
        return {"x": 1000, "y": 2000, "z": 12, "stage": "claimed", "user_id": user_id}

    def release_tile(self, service_id: str, user_id: str) -> Dict:
        """Mock releasing a tile."""
        self.last_user_id = user_id
        return {"x": 1000, "y": 2000, "z": 12, "stage": "released"}

    def submit_tile(self, service_id: str, user_id: str) -> Dict:
        """Mock submitting a tile."""
        self.last_user_id = user_id
        return {"x": 1000, "y": 2000, "z": 12, "stage": "submitted"}


def test_tile_assignment_user_parameter():
    """Test that tile_assignment correctly handles the user parameter."""

    # Create a test user
    test_user = User(
        user_id="test123",
        name="Test User",
        roles=["user"],
    )

    # Create mock store
    store = MockTileStore()

    # Test claiming a tile
    result = tile_assignment(
        zoom=12,
        x_range=(1000, 1010),
        y_range=(2000, 2010),
        stage="claim",
        store=ParameterReference(from_parameter="_openeo_store"),
        service_id="test_service",
        user_id=ParameterReference(from_parameter="_openeo_user"),
        named_parameters={"_openeo_user": test_user, "_openeo_store": store},
    )

    # Verify the store received just the user_id
    assert store.last_user_id == "test123"
    assert store.last_user_id is not test_user  # Not the full object
    assert isinstance(store.last_user_id, str)

    # Verify the result
    assert result["stage"] == "claimed"
    assert result["x"] == 1000
    assert result["y"] == 2000
    assert result["z"] == 12


def test_tile_assignment_direct_user_id():
    """Test that tile_assignment works with direct user_id."""

    # Create mock store
    store = MockTileStore()

    # Test claiming a tile with direct user_id
    result = tile_assignment(
        zoom=12,
        x_range=(1000, 1010),
        y_range=(2000, 2010),
        stage="claim",
        store=store,
        service_id="test_service",
        user_id="direct123",
    )

    # Verify the store received the user_id
    assert store.last_user_id == "direct123"
    assert isinstance(store.last_user_id, str)

    # Verify the result
    assert result["stage"] == "claimed"
    assert result["x"] == 1000
    assert result["y"] == 2000
    assert result["z"] == 12


def test_tile_assignment_user_parameter_missing():
    """Test error handling for missing user parameter."""

    # Create mock store
    store = MockTileStore()

    # Test with missing user parameter
    with pytest.raises(Exception) as exc_info:
        tile_assignment(
            zoom=12,
            x_range=(1000, 1010),
            y_range=(2000, 2010),
            stage="claim",
            store=store,
            service_id="test_service",
            user_id=ParameterReference(from_parameter="_openeo_user"),
            named_parameters={},
        )
    assert "missing" in str(exc_info.value).lower()
