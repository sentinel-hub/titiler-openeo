"""ABC Base services Store."""

import abc
from typing import Any, Dict, List, Optional, Tuple

from attrs import define, field
from starlette import status

from titiler.openeo.auth import User
from titiler.openeo.errors import OpenEOException


class TileAssignmentError(OpenEOException):
    """Base class for tile assignment errors."""

    pass


class NoTileAvailableError(TileAssignmentError):
    """Raised when no tiles are available for assignment."""

    def __init__(self, service_id: str, user_id: str, message: str = ""):
        """Initialize error with no tile available message."""
        super().__init__(
            message=f"No tile available for service_id: {service_id} and user_id: {user_id}. {message}",
            code="NoTileAvailable",
            status_code=status.HTTP_409_CONFLICT,
        )


class TileNotAssignedError(TileAssignmentError):
    """Raised when trying to release/submit a non-assigned tile."""

    def __init__(self, service_id: str, user_id: str):
        """Initialize error with no tile assigned message."""
        super().__init__(
            message=f"No tile assigned to user {user_id} for service {service_id}",
            code="TileNotAssigned",
            status_code=status.HTTP_404_NOT_FOUND,
        )


class TileAlreadyLockedError(TileAssignmentError):
    """Raised when trying to release a submitted tile."""

    def __init__(self, x: int, y: int, z: int, service_id: str, user_id: str):
        """Initialize error with tile already locked message."""
        super().__init__(
            message=f"Tile {x}/{y}/{z} is already locked for user {user_id} for service {service_id}",
            code="TileAlreadyLocked",
            status_code=status.HTTP_409_CONFLICT,
        )


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
    def update_tile(
        self, service_id: str, user_id: str, json_data: Dict[str, Any]
    ) -> Dict[str, Any]:
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

    @abc.abstractmethod
    def get_all_tiles(self, service_id: str) -> List[Dict[str, Any]]:
        """Get all tiles for a given service.

        Args:
            service_id: The service identifier

        Returns:
            List of dictionaries containing tile information including x, y, z coordinates,
            user_id (if assigned), status, and any additional metadata
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

    @abc.abstractmethod
    def track_user_login(self, user: User, provider: str) -> None:
        """Track user login activity.

        Args:
            user: The user that authenticated
            provider: The authentication provider (e.g. 'basic', 'oidc')
        """
        ...

    @abc.abstractmethod
    def get_user_tracking(
        self, user_id: str, provider: str
    ) -> Optional[Dict[str, Any]]:
        """Get user tracking information.

        Args:
            user_id: The user identifier
            provider: The authentication provider

        Returns:
            Dictionary containing tracking information or None if not found
            {
                "user_id": str,
                "provider": str,
                "first_login": datetime,
                "last_login": datetime,
                "login_count": int,
                "email": Optional[str],
                "name": Optional[str]
            }
        """
        ...
