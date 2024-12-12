"""ABC Base services Store."""

import abc
from typing import Any, Dict, List

from attrs import define, field


@define()
class ServicesStore(metaclass=abc.ABCMeta):
    """ABC Class defining STAC Backends."""

    store: Any = field()

    @abc.abstractmethod
    def get_service(self, service_id: str) -> Dict:
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

    # TODO: define input model
    @abc.abstractmethod
    def add_service(self, user_id: str, service: Dict, **kwargs) -> str:
        """Add Service."""
        ...

    @abc.abstractmethod
    def delete_service(self, service_id: str, **kwargs) -> bool:
        """Delete Service."""
        ...

    # @abc.abstractmethod
    # def update_service(self, user_id: str, item_id: str, val: Dict[str, Any], **kwargs) -> str:
    #     """Update Service."""
    #     ...
