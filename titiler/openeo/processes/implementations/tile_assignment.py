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
    control_user: bool = True,  # Optional parameter to enable user control
) -> Dict[str, Any]:
    """Assign XYZ tiles to users.

    Args:
        zoom: Fixed zoom level
        x_range: Range of X values [min, max]
        y_range: Range of Y values [min, max]
        stage: Stage of assignment (claim/release/submit)
        store: Tile assignment store instance
        service_id: Current service ID
        user_id: Current user ID
        control_user: Enable user verification for release/submit operations.
                    When True, only the user who claimed a tile can release/submit it.
                    Defaults to True.

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
    elif stage in ["release", "submit"]:
        if control_user:
            # Get current tile assignment to check ownership
            current_tile = store.get_user_tile(service_id, user_id)
            if not current_tile:
                raise TileNotAssignedError(service_id, user_id)

            # Get tile's assigned user
            tile_user = current_tile.get("user_id")
            if tile_user != user_id:
                raise TileNotAssignedError(service_id, user_id)

        # Perform the requested operation
        if stage == "release":
            return store.release_tile(service_id, user_id)
        else:  # submit
            return store.submit_tile(service_id, user_id)
    else:
        raise ValueError(f"Invalid stage: {stage}")
