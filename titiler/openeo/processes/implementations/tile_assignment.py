"""titiler.openeo.processes.implementations tile_assignment."""

from typing import Any, Dict, List, Optional, Tuple

from rio_tiler.models import ImageData

from ...services.base import TileAssignmentStore, TileNotAssignedError
from .core import process

__all__ = ["tile_assignment", "tiles_summary"]


@process
def tile_assignment(
    zoom: int,
    x_range: Tuple[int, int],
    y_range: Tuple[int, int],
    stage: str,
    store: TileAssignmentStore,
    service_id: str,
    user_id: str,
    data: Optional[
        Dict[str, Any]
    ] = None,  # Optional data for updating tile information
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
        data: Additional information to store with the tile (for update stage)

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
    elif stage in ["release", "submit", "force-release", "update"]:
        # Get current tile assignment
        current_tile = store.get_user_tile(service_id, user_id)
        if not current_tile:
            raise TileNotAssignedError(service_id=service_id, user_id=user_id)
        # Perform the requested operation
        if stage == "release":
            return store.release_tile(service_id, user_id)
        elif stage == "submit":
            return store.submit_tile(service_id, user_id)
        elif stage == "update":
            if not data:
                data = {}
            if isinstance(data, dict):
                # check tha each value in data is a valid ImageData
                if all(isinstance(v, ImageData) for v in data.values()):
                    # extract each ImageData's metadata
                    data = {
                        k: v.metadata if v.metadata else {} for k, v in data.items()
                    }
            return store.update_tile(service_id, user_id, data)
        else:  # force-release
            # Use the current tile's coordinates for force release
            return store.force_release_tile(
                service_id, current_tile["x"], current_tile["y"], current_tile["z"]
            )
    else:
        raise ValueError(f"Invalid stage: {stage}")


@process
def tiles_summary(
    store: TileAssignmentStore,
    service_id: str,
) -> List[Dict[str, Any]]:
    """Get a summary of all tiles information.

    Args:
        store: Tile assignment store instance
        service_id: Current service ID

    Returns:
        Dict containing information about all tiles including:
        - claimed: Dict[str, Dict] mapping user_ids to their assigned tile info
        - submitted: List[Dict] of submitted tiles with their metadata
    """
    return store.get_all_tiles(service_id)
