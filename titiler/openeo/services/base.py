"""ABC Base services Store."""

import abc
from typing import Any, Dict, List, Optional

from attrs import define, field


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
