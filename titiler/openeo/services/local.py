"""titiler.openeo.services Local.

NOTE: This should be used only for Testing Purposes.

"""

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from attrs import define, field

from ..models.auth import User
from .base import ServicesStore


@define
class LocalStore(ServicesStore):
    """Local Service STORE, for testing purposes."""

    store: Dict = field()
    tracking_store: Dict = field(factory=dict)

    def get_service(self, service_id: str) -> Optional[Dict]:
        """Return a specific Service."""
        if service_id not in self.store:
            return None
        return {
            "id": service_id,
            "user_id": self.store[service_id]["user_id"],
            **self.store[service_id]["service"],
        }

    def get_services(self, **kwargs) -> List[Dict]:
        """Return All Services."""
        return [
            {
                "id": service_id,
                "user_id": data["user_id"],
                **data["service"],
            }
            for service_id, data in self.store.items()
        ]

    def get_user_services(self, user_id: str, **kwargs) -> List[Dict]:
        """Return List Services for a user."""
        services = [
            {
                "id": service_id,
                "user_id": data["user_id"],
                **data["service"],
            }
            for service_id, data in self.store.items()
            if data["user_id"] == user_id
        ]
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

    def track_user_login(self, user: User, provider: str) -> None:
        """Track user login activity."""
        now = datetime.now(timezone.utc)
        key = (user.user_id, provider)

        if key in self.tracking_store:
            self.tracking_store[key]["last_login"] = now
            self.tracking_store[key]["login_count"] += 1
            self.tracking_store[key]["email"] = user.email
            self.tracking_store[key]["name"] = user.name
        else:
            self.tracking_store[key] = {
                "user_id": user.user_id,
                "provider": provider,
                "first_login": now,
                "last_login": now,
                "login_count": 1,
                "email": user.email,
                "name": user.name,
            }

    def get_user_tracking(
        self, user_id: str, provider: str
    ) -> Optional[Dict[str, Any]]:
        """Get user tracking information."""
        return self.tracking_store.get((user_id, provider))
