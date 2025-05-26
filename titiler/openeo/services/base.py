"""ABC Base services Store."""

import abc
from typing import Any, Dict, List, Optional, Tuple

from attrs import define, field


class TileAssignmentError(Exception):
    """Base class for tile assignment errors."""

    pass


class NoTileAvailableError(TileAssignmentError):
    """Raised when no tiles are available for assignment."""

    pass


class TileNotAssignedError(TileAssignmentError):
    """Raised when trying to release/submit a non-assigned tile."""

    pass


class TileAlreadyLockedError(TileAssignmentError):
    """Raised when trying to release a submitted tile."""

    pass


@define()
class TileAssignmentStore(metaclass=abc.ABCMeta):
    """ABC Class defining Tile Assignment operations."""

    store: Any = field()

    def __init__(self, store: Any):
        """Initialize the TileAssignmentStore.

        Args:
            store (Any): The store instance to be used by the service.
        """
        self.store = store

    @abc.abstractmethod
    def claim_tile(
        self,
        service_id: str,
        user_id: str,
        zoom: int,
        x_range: Tuple[int, int],
        y_range: Tuple[int, int],
    ) -> Dict[str, Any]:
        """Claim a tile for a user within given ranges.

        Args:
            service_id: The service identifier
            user_id: The user identifier
            zoom: The fixed zoom level
            x_range: Tuple of (min_x, max_x)
            y_range: Tuple of (min_y, max_y)

        Returns:
            Dict with x, y, z, and stage

        Raises:
            NoTileAvailableError: When no tiles are available
        """
        ...

    @abc.abstractmethod
    def release_tile(self, service_id: str, user_id: str) -> Dict[str, Any]:
        """Release a user's assigned tile.

        Args:
            service_id: The service identifier
            user_id: The user identifier

        Returns:
            Dict with x, y, z, and stage of released tile

        Raises:
            TileNotAssignedError: When user has no tile
            TileAlreadyLockedError: When tile is in submitted stage
        """
        ...

    @abc.abstractmethod
    def submit_tile(self, service_id: str, user_id: str) -> Dict[str, Any]:
        """Mark a tile as submitted.

        Args:
            service_id: The service identifier
            user_id: The user identifier

        Returns:
            Dict with x, y, z, and stage

        Raises:
            TileNotAssignedError: When user has no tile
        """
        ...

    @abc.abstractmethod
    def force_release_tile(
        self, service_id: str, x: int, y: int, z: int
    ) -> Dict[str, Any]:
        """Force release a tile regardless of its state.

        Args:
            service_id: The service identifier
            x: The tile x coordinate
            y: The tile y coordinate
            z: The tile z coordinate

        Returns:
            Dict with x, y, z, and stage of released tile

        Raises:
            TileNotAssignedError: When tile does not exist
        """
        ...

    @abc.abstractmethod
    def get_user_tile(self, service_id: str, user_id: str) -> Optional[Dict[str, Any]]:
        """Get a user's currently assigned tile.

        Args:
            service_id: The service identifier
            user_id: The user identifier

        Returns:
            Dict with x, y, z, and stage or None if no tile assigned
        """
        ...

    @abc.abstractmethod
    def update_tile(self, service_id: str, user_id: str, json_data: Dict[str, Any]) -> Dict[str, Any]:
        """Update a user's assigned tile with additional information.

        Args:
            service_id: The service identifier
            user_id: The user identifier
            json_data: Additional information to store with the tile

        Returns:
            Dict with x, y, z, stage and additional data

        Raises:
            TileNotAssignedError: When user has no tile
        """
        ...


@define()
class ServicesStore(metaclass=abc.ABCMeta):
    """ABC Class defining STAC Backends."""

    store: Any = field()

    def __init__(self, store: Any):
        """Initialize the ServicesStore.

        Args:
            store (Any): The store instance to be used by the service.
        """
        self.store = store

    @abc.abstractmethod
    def get_service(self, service_id: str) -> Optional[Dict]:
        """Return a specific Service."""
        ...

    @abc.abstractmethod
    def get_services(self, **kwargs) -> List[Dict]:
        """Return All Services."""
        ...

    @abc.abstractmethod
    def get_user_services(self, user_id: str, **kwargs) -> List[Dict]:
        """Return List Services for a user."""
        ...

    @abc.abstractmethod
    def add_service(self, user_id: str, service: Dict, **kwargs) -> str:
        """Add Service."""
        ...

    @abc.abstractmethod
    def delete_service(self, service_id: str, **kwargs) -> bool:
        """Delete Service."""
        ...

    @abc.abstractmethod
    def update_service(
        self, user_id: str, item_id: str, val: Dict[str, Any], **kwargs
    ) -> str:
        """Update Service."""
        ...
