"""titiler.openeo.services Local.

NOTE: This should be used only for Testing Purposes.

"""

import uuid
from copy import copy
from typing import Dict, List

from attrs import define, field

from .base import ServicesStore

default_services = {
    "d55ae9e5-83d7-41c2-ae56-e6c72d3b7da5": {
        "user_id": "anonymous",
        "service": {
            "title": "test",
            "description": None,
            "type": "XYZ",
            "enabled": True,
            "configuration": {},
            "plan": "trial",
            "budget": None,
            "process": {
                "process_graph": {
                    # TODO: create proper graph
                    "load1": {
                        "process_id": "load_collection",
                        "arguments": {
                            "id": "SENTINEL2_L2A",
                            "spatial_extent": {
                                "west": -10.740723669048181,
                                "east": 20.93035261581019,
                                "south": 38.250605037486906,
                                "north": 54.47085481675458,
                            },
                            "temporal_extent": ["2016-11-01T00:00:00Z", None],
                            "bands": ["B01"],
                        },
                    },
                    "save1": {
                        "process_id": "save_result",
                        "arguments": {"format": "PNG", "data": {"from_node": "load1"}},
                        "result": True,
                    },
                },
                "parameters": [],
            },
        },
    }
}


@define(kw_only=True)
class LocalStore(ServicesStore):
    """Local Service STORE, for testing purposes."""

    store: Dict = field(default=copy(default_services))

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
