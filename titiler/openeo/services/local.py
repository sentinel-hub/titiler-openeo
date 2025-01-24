"""titiler.openeo.services Local.

NOTE: This should be used only for Testing Purposes.

"""

import uuid
from typing import Any, Dict, List, Optional

from attrs import define, field

from .base import ServicesStore


@define()
class LocalStore(ServicesStore):
    """Local Service STORE, for testing purposes."""

    store: Dict = field()

    def get_service(self, service_id: str) -> Optional[Dict]:
        """Return a specific Service."""
        if service_id not in self.store:
            return None
        return {
            "id": service_id,
            **self.store[service_id]["service"],
        }

    def get_services(self, **kwargs) -> List[Dict]:
        """Return All Services."""
        return [
            {
                "id": service_id,
                **data["service"],
            }
            for service_id, data in self.store.items()
        ]

    def get_user_services(self, user_id: str, **kwargs) -> List[Dict]:
        """Return List Services for a user."""
        services = [
            {
                "id": service_id,
                **data["service"],
            }
            for service_id, data in self.store.items()
            if data["user_id"] == user_id
        ]
        assert services, f"Could not find service for user: {user_id}"
        return services

    def add_service(self, user_id: str, service: Dict, **kwargs) -> str:
        """Add Service."""
        service_id = str(uuid.uuid4())
        self.store[service_id] = {
            "user_id": user_id,
            "service": service,
        }
        return service_id

    def delete_service(self, service_id: str, **kwargs) -> bool:
        """Delete Service."""
        _ = self.store.pop(service_id)
        return True

    def update_service(
        self, user_id: str, item_id: str, val: Dict[str, Any], **kwargs
    ) -> str:
        """Update Service."""
        if item_id not in self.store:
            raise ValueError(f"Could not find service: {item_id}")

        if self.store[item_id]["user_id"] != user_id:
            raise ValueError(f"Service {item_id} does not belong to user {user_id}")

        self.store[item_id]["service"].update(val)
        return item_id
