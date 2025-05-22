"""titiler.openeo.processes.implementations tile_assignment."""

from typing import Any, Dict, Tuple

from ...services.base import TileAssignmentStore, TileNotAssignedError
from .core import process

__all__ = ["tile_assignment"]


@process
def tile_assignment(
    zoom: int,
    x_range: Tuple[int, int],
    y_range: Tuple[int, int],
    stage: str,
    store: TileAssignmentStore,
    service_id: str,
    user_id: str,
) -> Dict[str, Any]:
    """Assign XYZ tiles to users.

    Args:
        zoom: Fixed zoom level
        x_range: Range of X values [min, max]
        y_range: Range of Y values [min, max]
        stage: Stage of assignment (claim/release/submit/force-release)
        store: Tile assignment store instance
        service_id: Current service ID
        user_id: Current user ID

    Returns:
        Dict containing x, y, z coordinates and stage

    Raises:
        ValueError: When stage is invalid
        NoTileAvailableError: When no tiles are available for claiming
        TileNotAssignedError: When trying to release/submit a non-assigned tile
        TileAlreadyLockedError: When trying to release a submitted tile
    """
    if stage == "claim":
        return store.claim_tile(service_id, user_id, zoom, x_range, y_range)
    elif stage in ["release", "submit", "force-release"]:
        # Get current tile assignment
        current_tile = store.get_user_tile(service_id, user_id)
        if not current_tile:
            raise TileNotAssignedError(f"No tile assigned to user {user_id}")

        # Perform the requested operation
        if stage == "release":
            return store.release_tile(service_id, user_id)
        elif stage == "submit":
            return store.submit_tile(service_id, user_id)
        else:  # force-release
            # Use the current tile's coordinates for force release
            return store.force_release_tile(
                service_id,
                current_tile["x"],
                current_tile["y"],
                current_tile["z"]
            )
    else:
        raise ValueError(f"Invalid stage: {stage}")
