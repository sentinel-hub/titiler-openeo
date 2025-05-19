"""Test tile store implementations."""

import pytest

from titiler.openeo.services.base import (
    NoTileAvailableError,
    TileAlreadyLockedError,
    TileNotAssignedError,
)
from titiler.openeo.services.sqlalchemy_tile import SQLAlchemyTileStore


@pytest.fixture
def tile_store():
    """Create a SQLAlchemy tile store for testing."""
    store = SQLAlchemyTileStore("sqlite:///:memory:")
    return store


def test_claim_tile(tile_store):
    """Test claiming a tile."""
    # Claim a tile
    tile = tile_store.claim_tile(
        service_id="test_service",
        user_id="test_user",
        zoom=12,
        x_range=(0, 1),
        y_range=(0, 1),
    )

    # Verify tile properties
    assert isinstance(tile, dict)
    assert "x" in tile
    assert "y" in tile
    assert "z" in tile
    assert "stage" in tile
    assert tile["z"] == 12
    assert 0 <= tile["x"] <= 1
    assert 0 <= tile["y"] <= 1
    assert tile["stage"] == "claimed"

    # Verify the same tile is returned for the same user
    tile2 = tile_store.claim_tile(
        service_id="test_service",
        user_id="test_user",
        zoom=12,
        x_range=(0, 1),
        y_range=(0, 1),
    )
    assert tile == tile2


def test_claim_tile_no_available(tile_store):
    """Test claiming when no tiles are available."""
    # Claim all available tiles
    tile_store.claim_tile(
        service_id="test_service",
        user_id="user1",
        zoom=12,
        x_range=(0, 0),
        y_range=(0, 0),
    )

    # Try to claim a tile with another user
    with pytest.raises(NoTileAvailableError):
        tile_store.claim_tile(
            service_id="test_service",
            user_id="user2",
            zoom=12,
            x_range=(0, 0),
            y_range=(0, 0),
        )


def test_release_tile(tile_store):
    """Test releasing a tile."""
    # First claim a tile
    tile = tile_store.claim_tile(
        service_id="test_service",
        user_id="test_user",
        zoom=12,
        x_range=(0, 1),
        y_range=(0, 1),
    )

    # Release the tile
    released = tile_store.release_tile(service_id="test_service", user_id="test_user")

    # Verify release response
    assert released["x"] == tile["x"]
    assert released["y"] == tile["y"]
    assert released["z"] == tile["z"]
    assert released["stage"] == "released"

    # Verify tile is no longer assigned
    assert (
        tile_store.get_user_tile(service_id="test_service", user_id="test_user") is None
    )


def test_release_tile_not_assigned(tile_store):
    """Test releasing a tile that isn't assigned."""
    with pytest.raises(TileNotAssignedError):
        tile_store.release_tile(service_id="test_service", user_id="test_user")


def test_submit_tile(tile_store):
    """Test submitting a tile."""
    # First claim a tile
    tile = tile_store.claim_tile(
        service_id="test_service",
        user_id="test_user",
        zoom=12,
        x_range=(0, 1),
        y_range=(0, 1),
    )

    # Submit the tile
    submitted = tile_store.submit_tile(service_id="test_service", user_id="test_user")

    # Verify submit response
    assert submitted["x"] == tile["x"]
    assert submitted["y"] == tile["y"]
    assert submitted["z"] == tile["z"]
    assert submitted["stage"] == "submitted"

    # Verify can't release submitted tile
    with pytest.raises(TileAlreadyLockedError):
        tile_store.release_tile(service_id="test_service", user_id="test_user")


def test_submit_tile_not_assigned(tile_store):
    """Test submitting a tile that isn't assigned."""
    with pytest.raises(TileNotAssignedError):
        tile_store.submit_tile(service_id="test_service", user_id="test_user")


def test_get_user_tile(tile_store):
    """Test getting a user's tile."""
    # Initially no tile
    assert (
        tile_store.get_user_tile(service_id="test_service", user_id="test_user") is None
    )

    # Claim a tile
    tile = tile_store.claim_tile(
        service_id="test_service",
        user_id="test_user",
        zoom=12,
        x_range=(0, 1),
        y_range=(0, 1),
    )

    # Verify get_user_tile returns the same tile
    user_tile = tile_store.get_user_tile(service_id="test_service", user_id="test_user")
    assert user_tile == tile


def test_multiple_services(tile_store):
    """Test tile assignments for multiple services."""
    # Claim tiles for different services
    # Claim tiles with different ranges
    tile1 = tile_store.claim_tile(
        service_id="service1",
        user_id="test_user",
        zoom=12,
        x_range=(0, 1),
        y_range=(0, 1),
    )
    tile2 = tile_store.claim_tile(
        service_id="service2",
        user_id="test_user",
        zoom=12,
        x_range=(1, 2),
        y_range=(1, 2),
    )

    # Verify tiles are different
    assert tile1 != tile2

    # Verify can get both tiles
    assert tile_store.get_user_tile("service1", "test_user") == tile1
    assert tile_store.get_user_tile("service2", "test_user") == tile2


def test_random_assignment(tile_store):
    """Test that tile assignment is properly randomized."""
    # Define a range that gives us enough tiles to test randomization
    x_range = (0, 2)  # 3 possible x values
    y_range = (0, 2)  # 3 possible y values
    zoom = 12

    # Claim tiles with different users
    tiles = []
    for i in range(5):  # Claim 5 tiles
        tile = tile_store.claim_tile(
            service_id="test_service",
            user_id=f"user_{i}",
            zoom=zoom,
            x_range=x_range,
            y_range=y_range,
        )
        tiles.append(tile)

    # Verify tiles are within range
    for tile in tiles:
        assert x_range[0] <= tile["x"] <= x_range[1]
        assert y_range[0] <= tile["y"] <= y_range[1]
        assert tile["z"] == zoom

    # Verify we got different tiles (at least some should be different)
    # Convert tiles to coordinate tuples for easy comparison
    coords = [(t["x"], t["y"]) for t in tiles]
    unique_coords = set(coords)
    assert len(unique_coords) > 1, "All tiles were assigned to the same coordinates"

    # Verify distribution across the range
    x_values = [t["x"] for t in tiles]
    y_values = [t["y"] for t in tiles]
    assert len(set(x_values)) > 1, "All tiles have the same x coordinate"
    assert len(set(y_values)) > 1, "All tiles have the same y coordinate"


def test_complex_scenario(tile_store):
    """Test a complex scenario with multiple users and various operations."""
    # Setup a 2x2 grid of tiles
    x_range = (0, 1)
    y_range = (0, 1)
    zoom = 12

    # Step 1: User1 and User2 claim tiles
    tile1 = tile_store.claim_tile(
        service_id="test_service",
        user_id="user1",
        zoom=zoom,
        x_range=x_range,
        y_range=y_range,
    )
    tile2 = tile_store.claim_tile(
        service_id="test_service",
        user_id="user2",
        zoom=zoom,
        x_range=x_range,
        y_range=y_range,
    )

    # Verify tiles are different
    assert tile1 != tile2

    # Step 2: User1 releases their tile
    released_tile = tile_store.release_tile(service_id="test_service", user_id="user1")
    assert released_tile["stage"] == "released"

    # Step 3: User3 claims a tile (should be able to get User1's released tile)
    tile3 = tile_store.claim_tile(
        service_id="test_service",
        user_id="user3",
        zoom=zoom,
        x_range=x_range,
        y_range=y_range,
    )

    # Step 4: User2 submits their tile
    submitted = tile_store.submit_tile(service_id="test_service", user_id="user2")
    assert submitted["stage"] == "submitted"

    # Step 5: User2 tries to release their submitted tile (should fail)
    with pytest.raises(TileAlreadyLockedError):
        tile_store.release_tile(service_id="test_service", user_id="user2")

    # Step 6: User4 claims a tile
    tile_store.claim_tile(
        service_id="test_service",
        user_id="user4",
        zoom=zoom,
        x_range=x_range,
        y_range=y_range,
    )

    # Step 7: User6 claims the last available tile
    tile_store.claim_tile(
        service_id="test_service",
        user_id="user6",
        zoom=zoom,
        x_range=x_range,
        y_range=y_range,
    )

    # Step 8: User5 tries to claim a tile (should fail as all are taken)
    with pytest.raises(NoTileAvailableError):
        tile_store.claim_tile(
            service_id="test_service",
            user_id="user5",
            zoom=zoom,
            x_range=x_range,
            y_range=y_range,
        )

    # Step 9: User3 releases their tile
    tile_store.release_tile(service_id="test_service", user_id="user3")

    # Step 10: User5 can now claim the released tile
    tile5 = tile_store.claim_tile(
        service_id="test_service",
        user_id="user5",
        zoom=zoom,
        x_range=x_range,
        y_range=y_range,
    )
    assert tile5["x"] == tile3["x"]
    assert tile5["y"] == tile3["y"]

    # Final state verification
    # - User2's tile should be submitted
    # - User4's tile should be claimed
    # - User5's tile should be claimed
    # - User6's tile should be claimed
    # - User1 and User3 should have no tiles

    assert tile_store.get_user_tile("test_service", "user1") is None
    assert tile_store.get_user_tile("test_service", "user2")["stage"] == "submitted"
    assert tile_store.get_user_tile("test_service", "user3") is None
    assert tile_store.get_user_tile("test_service", "user4")["stage"] == "claimed"
    assert tile_store.get_user_tile("test_service", "user5")["stage"] == "claimed"
    assert tile_store.get_user_tile("test_service", "user6")["stage"] == "claimed"
