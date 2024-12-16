"""titiler.openeo.services Local.

NOTE: This should be used only for Testing Purposes.

"""

import uuid
from typing import Dict, List

from attrs import define, field

from .base import ServicesStore


@define()
class LocalStore(ServicesStore):
    """Local Service STORE, for testing purposes."""

    store: Dict = field()

    def get_service(self, service_id: str) -> Dict:
        """Return a specific Service."""
        assert service_id in self.store, f"Could not find service: {service_id}"
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
