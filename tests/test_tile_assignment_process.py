"""Test tile assignment process."""

from typing import Any, Dict, List

import pytest

from titiler.openeo.processes.implementations.tile_assignment import (
    tile_assignment,
    tiles_summary,
)
from titiler.openeo.services.base import (
    NoTileAvailableError,
    TileAlreadyLockedError,
    TileAssignmentStore,
    TileNotAssignedError,
)


class MockTileStore(TileAssignmentStore):
    """Mock tile store for testing."""

    def __init__(self):
        """Initialize with empty assignments."""
        super().__init__(store=None)
        self.assignments = {}

    def claim_tile(self, service_id, user_id, zoom, x_range, y_range):
        """Mock claim tile."""
        key = f"{service_id}:{user_id}"
        if key in self.assignments:
            return self.assignments[key]

        if x_range[0] > x_range[1] or y_range[0] > y_range[1]:
            raise NoTileAvailableError(service_id, user_id, "Range is invalid")

        tile = {
            "x": x_range[0],
            "y": y_range[0],
            "z": zoom,
            "stage": "claimed",
            "user_id": user_id,
        }
        self.assignments[key] = tile
        return tile

    def release_tile(self, service_id, user_id):
        """Mock release tile."""
        # First check if the user has their own tile
        key = f"{service_id}:{user_id}"
        if key in self.assignments:
            tile = self.assignments[key]
            if tile["stage"] == "submitted":
                raise TileAlreadyLockedError(0, 0, 0, service_id, user_id)
            tile = {**tile, "stage": "released"}
            del self.assignments[key]
            return tile

        # If no tile assigned to this user, find any claimed tile
        for k, tile in self.assignments.items():
            if k.startswith(f"{service_id}:"):
                if tile["stage"] == "submitted":
                    raise TileAlreadyLockedError(0, 0, 0, service_id, user_id)
                tile = {**tile, "stage": "released"}
                del self.assignments[k]
                return tile

        raise TileNotAssignedError(service_id, user_id)

    def submit_tile(self, service_id, user_id):
        """Mock submit tile."""
        # First check if the user has their own tile
        key = f"{service_id}:{user_id}"
        if key in self.assignments:
            tile = self.assignments[key]
            tile["stage"] = "submitted"
            return tile

        # If no tile assigned to this user, find any claimed tile
        for k, tile in self.assignments.items():
            if k.startswith(f"{service_id}:"):
                tile["stage"] = "submitted"
                return tile

        raise TileNotAssignedError(service_id, user_id)

    def force_release_tile(self, service_id, x, y, z):
        """Mock force release tile."""
        # Find tile by coordinates
        for key, tile in self.assignments.items():
            if (
                key.startswith(f"{service_id}:")
                and tile["x"] == x
                and tile["y"] == y
                and tile["z"] == z
            ):
                released_tile = {**tile, "stage": "released"}
                del self.assignments[key]
                return released_tile
        raise TileNotAssignedError("No tile found with these coordinates")

    def get_user_tile(self, service_id, user_id):
        """Mock get user tile."""
        key = f"{service_id}:{user_id}"
        return self.assignments.get(key)

    def update_tile(self, service_id, user_id, json_data):
        """Mock update tile with additional information."""
        key = f"{service_id}:{user_id}"
        if key not in self.assignments:
            raise TileNotAssignedError(f"No tile assigned to user {user_id}")

        tile = self.assignments[key]
        updated_tile = {**tile}
        updated_tile.update(json_data)
        self.assignments[key] = updated_tile
        return updated_tile

    def get_all_tiles(self, service_id: str) -> List[Dict[str, Any]]:
        """Mock get all tiles.

        Args:
            service_id: The service identifier

        Returns:
            List of dictionaries containing tile information including x, y, z coordinates,
            user_id (if assigned), status (if submitted), and any additional metadata
        """
        tiles = []
        for key, tile in self.assignments.items():
            if key.startswith(f"{service_id}:"):
                tiles.append(tile)
        return tiles


@pytest.fixture
def store():
    """Create a mock tile store."""
    return MockTileStore()


def test_claim_tile(store):
    """Test claiming a tile through the process."""
    result = tile_assignment(
        zoom=12,
        x_range=(0, 1),
        y_range=(0, 1),
        stage="claim",
        store=store,
        service_id="test_service",
        user_id="test_user",
    )

    assert isinstance(result, dict)
    assert result["x"] == 0
    assert result["y"] == 0
    assert result["z"] == 12
    assert result["stage"] == "claimed"


def test_release_tile(store):
    """Test releasing a tile through the process."""
    # First claim a tile
    tile_assignment(
        zoom=12,
        x_range=(0, 1),
        y_range=(0, 1),
        stage="claim",
        store=store,
        service_id="test_service",
        user_id="test_user",
    )

    # Then release it
    result = tile_assignment(
        zoom=12,  # These parameters aren't used for release
        x_range=(0, 1),
        y_range=(0, 1),
        stage="release",
        store=store,
        service_id="test_service",
        user_id="test_user",
    )

    assert result["stage"] == "released"


def test_submit_tile(store):
    """Test submitting a tile through the process."""
    # First claim a tile
    claimed = tile_assignment(
        zoom=12,
        x_range=(0, 1),
        y_range=(0, 1),
        stage="claim",
        store=store,
        service_id="test_service",
        user_id="test_user",
    )

    # Then submit it
    result = tile_assignment(
        zoom=12,  # These parameters aren't used for submit
        x_range=(0, 1),
        y_range=(0, 1),
        stage="submit",
        store=store,
        service_id="test_service",
        user_id="test_user",
    )

    assert result["x"] == claimed["x"]
    assert result["y"] == claimed["y"]
    assert result["z"] == claimed["z"]
    assert result["stage"] == "submitted"


def test_invalid_stage(store):
    """Test invalid stage parameter."""
    with pytest.raises(ValueError, match="Invalid stage"):
        tile_assignment(
            zoom=12,
            x_range=(0, 1),
            y_range=(0, 1),
            stage="invalid",
            store=store,
            service_id="test_service",
            user_id="test_user",
        )


def test_no_tiles_available(store):
    """Test when no tiles are available."""
    with pytest.raises(NoTileAvailableError):
        tile_assignment(
            zoom=12,
            x_range=(1, 0),  # Invalid range
            y_range=(0, 1),
            stage="claim",
            store=store,
            service_id="test_service",
            user_id="test_user",
        )


def test_release_not_assigned(store):
    """Test releasing a tile that isn't assigned."""
    with pytest.raises(TileNotAssignedError):
        tile_assignment(
            zoom=12,
            x_range=(0, 1),
            y_range=(0, 1),
            stage="release",
            store=store,
            service_id="test_service",
            user_id="test_user",
        )


def test_submit_not_assigned(store):
    """Test submitting a tile that isn't assigned."""
    with pytest.raises(TileNotAssignedError):
        tile_assignment(
            zoom=12,
            x_range=(0, 1),
            y_range=(0, 1),
            stage="submit",
            store=store,
            service_id="test_service",
            user_id="test_user",
        )


def test_unauthorized_release(store):
    """Test releasing another user's tile."""
    # First user claims a tile
    tile_assignment(
        zoom=12,
        x_range=(0, 1),
        y_range=(0, 1),
        stage="claim",
        store=store,
        service_id="test_service",
        user_id="user1",
    )

    # Second user tries to release it
    with pytest.raises(TileNotAssignedError, match="No tile assigned to user user2"):
        tile_assignment(
            zoom=12,
            x_range=(0, 1),
            y_range=(0, 1),
            stage="release",
            store=store,
            service_id="test_service",
            user_id="user2",
        )


def test_unauthorized_submit(store):
    """Test submitting another user's tile."""
    # First user claims a tile
    tile_assignment(
        zoom=12,
        x_range=(0, 1),
        y_range=(0, 1),
        stage="claim",
        store=store,
        service_id="test_service",
        user_id="user1",
    )

    # Second user tries to submit it
    with pytest.raises(TileNotAssignedError, match="No tile assigned to user user2"):
        tile_assignment(
            zoom=12,
            x_range=(0, 1),
            y_range=(0, 1),
            stage="submit",
            store=store,
            service_id="test_service",
            user_id="user2",
        )


def test_force_release_submitted_tile(store):
    """Test force-releasing a submitted tile."""
    # First claim and submit a tile
    claimed = tile_assignment(
        zoom=12,
        x_range=(0, 1),
        y_range=(0, 1),
        stage="claim",
        store=store,
        service_id="test_service",
        user_id="test_user",
    )
    tile_assignment(
        zoom=12,
        x_range=(0, 1),
        y_range=(0, 1),
        stage="submit",
        store=store,
        service_id="test_service",
        user_id="test_user",
    )

    # Force release the tile - this should work even though it's submitted
    result = tile_assignment(
        zoom=12,
        x_range=(0, 1),
        y_range=(0, 1),
        stage="force-release",
        store=store,
        service_id="test_service",
        user_id="test_user",
    )

    assert result["x"] == claimed["x"]
    assert result["y"] == claimed["y"]
    assert result["z"] == claimed["z"]
    assert result["stage"] == "released"

    # Verify tile is gone
    assert store.get_user_tile("test_service", "test_user") is None


def test_force_release_nonexistent_tile(store):
    """Test force-releasing a tile that doesn't exist."""
    with pytest.raises(TileNotAssignedError):
        tile_assignment(
            zoom=12,
            x_range=(0, 1),
            y_range=(0, 1),
            stage="force-release",
            store=store,
            service_id="test_service",
            user_id="test_user",
        )


def test_update_tile(store):
    """Test updating a tile with additional data."""
    # First claim a tile
    claimed = tile_assignment(
        zoom=12,
        x_range=(0, 1),
        y_range=(0, 1),
        stage="claim",
        store=store,
        service_id="test_service",
        user_id="test_user",
    )

    # Update the tile with additional data
    json_data = {"progress": 50, "metadata": {"timestamp": "2025-05-26T12:00:00Z"}}
    result = tile_assignment(
        zoom=12,
        x_range=(0, 1),
        y_range=(0, 1),
        stage="update",
        store=store,
        service_id="test_service",
        user_id="test_user",
        data=json_data,
    )

    # Verify base tile info is preserved
    assert result["x"] == claimed["x"]
    assert result["y"] == claimed["y"]
    assert result["z"] == claimed["z"]
    assert result["stage"] == "claimed"

    # Verify additional data is included
    assert result["progress"] == 50
    assert result["metadata"]["timestamp"] == "2025-05-26T12:00:00Z"


def test_update_tile_not_assigned(store):
    """Test updating a tile that isn't assigned."""
    with pytest.raises(TileNotAssignedError):
        tile_assignment(
            zoom=12,
            x_range=(0, 1),
            y_range=(0, 1),
            stage="update",
            store=store,
            service_id="test_service",
            user_id="test_user",
            data={"progress": 50},
        )


def test_unauthorized_update(store):
    """Test updating another user's tile."""
    # First user claims a tile
    tile_assignment(
        zoom=12,
        x_range=(0, 1),
        y_range=(0, 1),
        stage="claim",
        store=store,
        service_id="test_service",
        user_id="user1",
    )

    # Second user tries to update it
    with pytest.raises(TileNotAssignedError, match="No tile assigned to user user2"):
        tile_assignment(
            zoom=12,
            x_range=(0, 1),
            y_range=(0, 1),
            stage="update",
            store=store,
            service_id="test_service",
            user_id="user2",
            data={"progress": 50},
        )


def test_tiles_summary_empty(store):
    """Test getting summary of tiles when there are no tiles."""
    summary = tiles_summary(store=store, service_id="test_service")

    assert isinstance(summary, dict)
    assert summary["claimed"] == []
    assert summary["submitted"] == []


def test_tiles_summary_with_tiles(store):
    """Test getting summary of tiles with various states."""
    # Claim a tile for user1
    tile1 = tile_assignment(
        zoom=12,
        x_range=(0, 1),
        y_range=(0, 1),
        stage="claim",
        store=store,
        service_id="test_service",
        user_id="user1",
    )

    # Claim and submit a tile for user2
    tile2 = tile_assignment(
        zoom=12,
        x_range=(1, 2),
        y_range=(1, 2),
        stage="claim",
        store=store,
        service_id="test_service",
        user_id="user2",
    )
    tile_assignment(
        zoom=12,
        x_range=(1, 2),
        y_range=(1, 2),
        stage="submit",
        store=store,
        service_id="test_service",
        user_id="user2",
    )

    # Add metadata to tile2
    metadata = {"progress": 100, "timestamp": "2025-06-02T12:00:00Z"}
    tile_assignment(
        zoom=12,
        x_range=(1, 2),
        y_range=(1, 2),
        stage="update",
        store=store,
        service_id="test_service",
        user_id="user2",
        data={"metadata": metadata},
    )

    summary = tiles_summary(store=store, service_id="test_service")

    # Check
    assert isinstance(summary, List)
    assert len(summary) == 2
    assert summary[0]["x"] == tile1["x"]
    assert summary[0]["y"] == tile1["y"]
    assert summary[0]["z"] == tile1["z"]
    assert summary[0]["user_id"] == "user1"
    assert summary[0]["stage"] == "claimed"
    assert summary[1]["x"] == tile2["x"]
    assert summary[1]["y"] == tile2["y"]
    assert summary[1]["z"] == tile2["z"]
    assert summary[1]["user_id"] == "user2"
    assert summary[1]["stage"] == "submitted"

