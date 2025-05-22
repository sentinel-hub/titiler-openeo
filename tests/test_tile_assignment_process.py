"""Test tile assignment process."""

import pytest

from titiler.openeo.processes.implementations.tile_assignment import tile_assignment
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
            raise NoTileAvailableError("Invalid ranges")

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
                raise TileAlreadyLockedError("Tile is submitted")
            tile = {**tile, "stage": "released"}
            del self.assignments[key]
            return tile

        # If no tile assigned to this user, find any claimed tile
        for k, tile in self.assignments.items():
            if k.startswith(f"{service_id}:"):
                if tile["stage"] == "submitted":
                    raise TileAlreadyLockedError("Tile is submitted")
                tile = {**tile, "stage": "released"}
                del self.assignments[k]
                return tile

        raise TileNotAssignedError("No tile assigned")

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

        raise TileNotAssignedError("No tile assigned")

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
