"""titiler.openeo.services Local.

NOTE: This should be used only for Testing Purposes.

"""

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from attrs import define, field

from ..models.auth import User
from .base import ServicesStore, UdpStore


def load_local_store_data(path: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Load local store data in the structured layout."""
    try:
        data = json.load(open(path))
    except FileNotFoundError:
        return {}, {}

    return data.get("services", {}), data.get("udp_definitions", {})


def _json_default(value: Any) -> Any:
    """Serialize non-JSON-native values."""
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(f"Object of type {type(value)} is not JSON serializable")


@define
class LocalServiceStore(ServicesStore):
    """Local Service STORE, for testing purposes."""

    store: Dict = field()
    tracking_store: Dict = field(factory=dict)
    path: Optional[str] = field(default=None, kw_only=True)

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
        self._persist()
        return service_id

    def delete_service(self, service_id: str, **kwargs) -> bool:
        """Delete Service."""
        _ = self.store.pop(service_id)
        self._persist()
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
        self._persist()
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
        self._persist()

    def get_user_tracking(
        self, user_id: str, provider: str
    ) -> Optional[Dict[str, Any]]:
        """Get user tracking information."""
        return self.tracking_store.get((user_id, provider))

    def _persist(self) -> None:
        """Write updated services back to disk while preserving UDP data."""
        if not self.path:
            return

        _, udp_definitions = load_local_store_data(self.path)
        data = {"services": self.store, "udp_definitions": udp_definitions}
        with open(self.path, "w") as f:
            json.dump(data, f, default=_json_default)


@define
class LocalUdpStore(UdpStore):
    """In-memory UDP Store, for testing purposes."""

    store: Dict[str, Dict[str, Any]] = field(factory=dict)
    path: Optional[str] = field(default=None, kw_only=True)

    def list_udps(
        self, user_id: str, limit: int = 100, offset: int = 0
    ) -> List[Dict[str, Any]]:
        """List UDP definitions for a user."""
        udps = [
            self._to_dict(udp_id, data)
            for udp_id, data in self.store.items()
            if data["user_id"] == user_id
        ]
        # Sort by created_at descending to mirror other stores
        udps.sort(key=lambda item: item["created_at"], reverse=True)
        return udps[offset : offset + limit]

    def get_udp(self, user_id: str, udp_id: str) -> Optional[Dict[str, Any]]:
        """Get a single UDP definition."""
        data = self.store.get(udp_id)
        if data is None or data["user_id"] != user_id:
            return None
        return self._to_dict(udp_id, data)

    def upsert_udp(
        self,
        user_id: str,
        udp_id: str,
        process_graph: Dict[str, Any],
        summary: Optional[str] = None,
        description: Optional[str] = None,
        parameters: Optional[List[Dict[str, Any]]] = None,
        returns: Optional[Dict[str, Any]] = None,
        categories: Optional[List[str]] = None,
        deprecated: bool = False,
        experimental: bool = False,
        exceptions: Optional[Dict[str, Any]] = None,
        examples: Optional[List[Dict[str, Any]]] = None,
        links: Optional[List[Dict[str, Any]]] = None,
    ) -> str:
        """Create or replace a UDP for a user."""
        now = datetime.utcnow()
        existing = self.store.get(udp_id)
        if existing is not None and existing["user_id"] != user_id:
            raise ValueError(f"UDP {udp_id} does not belong to user {user_id}")

        if existing is not None:
            existing["process_graph"] = process_graph
            existing["parameters"] = parameters
            existing["summary"] = summary
            existing["description"] = description
            existing["returns"] = returns
            existing["categories"] = categories or []
            existing["deprecated"] = deprecated
            existing["experimental"] = experimental
            existing["exceptions"] = exceptions
            existing["examples"] = examples
            existing["links"] = links
            existing["updated_at"] = now
        else:
            self.store[udp_id] = {
                "user_id": user_id,
                "process_graph": process_graph,
                "parameters": parameters,
                "summary": summary,
                "description": description,
                "returns": returns,
                "categories": categories or [],
                "deprecated": deprecated,
                "experimental": experimental,
                "exceptions": exceptions,
                "examples": examples,
                "links": links,
                "created_at": now,
                "updated_at": now,
            }
        self._persist()
        return udp_id

    def delete_udp(self, user_id: str, udp_id: str) -> bool:
        """Delete a UDP for a user."""
        existing = self.store.get(udp_id)
        if existing is None or existing["user_id"] != user_id:
            raise ValueError(f"Could not find UDP {udp_id} for user {user_id}")
        self.store.pop(udp_id)
        self._persist()
        return True

    def _to_dict(self, udp_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """Serialize UDP data to dict with id."""
        return {
            "id": udp_id,
            "user_id": data["user_id"],
            "process_graph": data["process_graph"],
            "parameters": data.get("parameters"),
            "summary": data.get("summary"),
            "description": data.get("description"),
            "returns": data.get("returns"),
            "categories": data.get("categories", []),
            "deprecated": data.get("deprecated", False),
            "experimental": data.get("experimental", False),
            "exceptions": data.get("exceptions"),
            "examples": data.get("examples"),
            "links": data.get("links"),
            "created_at": data.get("created_at"),
            "updated_at": data.get("updated_at"),
        }

    def _persist(self) -> None:
        """Write updated UDPs back to disk while preserving services."""
        if not self.path:
            return

        services, _ = load_local_store_data(self.path)
        data = {"services": services, "udp_definitions": self.store}
        with open(self.path, "w") as f:
            json.dump(data, f, default=_json_default)
