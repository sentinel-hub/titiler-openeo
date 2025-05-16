"""titiler.openeo.processes.implementations tile_assignment."""

from typing import Any, Dict, Tuple

from ...services.base import TileAssignmentStore
from .core import process

__all__ = ["tile_assignment"]


@process
def tile_assignment(
    zoom: int,
    x_range: Tuple[int, int],
    y_range: Tuple[int, int],
    stage: str,
    store: TileAssignmentStore,  # Injected by the framework
    service_id: str,  # Injected by the framework
    user_id: str,  # Injected by the framework
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
    elif stage == "release":
        return store.release_tile(service_id, user_id)
    elif stage == "submit":
        return store.submit_tile(service_id, user_id)
    else:
        raise ValueError(f"Invalid stage: {stage}")
