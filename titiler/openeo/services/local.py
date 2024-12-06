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
            "parameters": [
                {"description": "", "name": "spatial_extent_east", "schema": {}},
                {"description": "", "name": "spatial_extent_north", "schema": {}},
                {"description": "", "name": "spatial_extent_south", "schema": {}},
                {"description": "", "name": "spatial_extent_west", "schema": {}},
            ],
            "type": "XYZ",
            "title": "simple_3-bands_rgb_dynamic_bbox",
            "enabled": True,
            "process_graph": {
                "1": {
                    "arguments": {
                        "bands": ["B04", "B03", "B02"],
                        "id": "SENTINEL2_L2A",
                        "spatial_extent": {
                            "east": {"from_parameter": "spatial_extent_east"},
                            "north": {"from_parameter": "spatial_extent_north"},
                            "south": {"from_parameter": "spatial_extent_south"},
                            "west": {"from_parameter": "spatial_extent_west"},
                        },
                        "temporal_extent": [
                            "2022-03-26T00:00:00Z",
                            "2022-03-26T23:59:59Z",
                        ],
                    },
                    "process_id": "load_collection",
                },
                "2": {
                    "arguments": {
                        "data": {"from_node": "3"},
                        "format": "PNG",
                        "options": {"datatype": "byte"},
                    },
                    "process_id": "save_result",
                    "result": True,
                },
                "3": {
                    "arguments": {
                        "data": {"from_node": "1"},
                        "process": {
                            "process_graph": {
                                "1": {
                                    "arguments": {
                                        "inputMax": 0.4,
                                        "inputMin": 0,
                                        "outputMax": 255,
                                        "x": {"from_parameter": "x"},
                                    },
                                    "process_id": "linear_scale_range",
                                    "result": True,
                                }
                            }
                        },
                    },
                    "process_id": "apply",
                },
            },
        },
    },
    "31cbbbc1-9095-42f8-b4a5-fd36a653adaa": {
        "user_id": "anonymous",
        "service": {
            "parameters": [],
            "title": "simple_3-bands_rgb",
            "type": "XYZ",
            "enabled": True,
            "process_graph": {
                "1": {
                    "process_id": "load_collection",
                    "arguments": {
                        "bands": ["B05", "B04", "B03"],
                        "id": "SENTINEL2_L2A",
                        "spatial_extent": {
                            "west": 14.503132250376241,
                            "south": 45.98989222284457,
                            "east": 14.578437275398317,
                            "north": 46.04381770188389,
                        },
                        "temporal_extent": [
                            "2022-03-26T00:00:00Z",
                            "2022-03-26T23:59:59Z",
                        ],
                    },
                },
                "2": {
                    "process_id": "save_result",
                    "arguments": {
                        "data": {"from_node": "3"},
                        "format": "PNG",
                        "options": {"datatype": "byte"},
                    },
                    "result": True,
                },
                "3": {
                    "process_id": "apply",
                    "arguments": {
                        "data": {"from_node": "1"},
                        "process": {
                            "process_graph": {
                                "1": {
                                    "process_id": "linear_scale_range",
                                    "arguments": {
                                        "inputMax": 0.4,
                                        "inputMin": 0,
                                        "outputMax": 255,
                                        "x": {"from_parameter": "x"},
                                    },
                                    "result": True,
                                }
                            }
                        },
                    },
                },
            },
        },
    },
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
