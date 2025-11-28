"""Tests for local JSON store namespace separation and compatibility."""

import json
from pathlib import Path

from titiler.openeo.services import get_store, get_udp_store


def test_structured_layout_read_and_preserve(tmp_path: Path):
    """New structured layout keeps namespaces separate on read/write."""
    path = tmp_path / "store.json"
    path.write_text(
        json.dumps(
            {
                "services": {"svc": {"user_id": "u1", "service": {"n": 1}}},
                "udp_definitions": {
                    "udp": {
                        "user_id": "u2",
                        "process_graph": {"p": 1},
                        "created_at": "2024-01-01T00:00:00Z",
                        "updated_at": "2024-01-01T00:00:00Z",
                    }
                },
            }
        )
    )

    service_store = get_store(str(path))
    udp_store = get_udp_store(str(path))

    assert service_store.get_service("udp") is None
    assert udp_store.get_udp(user_id="u2", udp_id="udp") is not None

    new_service_id = service_store.add_service(user_id="u3", service={"n": 2})
    data = json.loads(path.read_text())
    assert "udp" in data["udp_definitions"]  # preserved
    assert new_service_id in data["services"]


def test_ids_can_collide_across_namespaces(tmp_path: Path):
    """Service IDs and UDP IDs can share the same value without collision."""
    path = tmp_path / "store.json"

    service_store = get_store(str(path))
    service_id = service_store.add_service(user_id="user", service={"n": 1})

    udp_store = get_udp_store(str(path))
    udp_store.upsert_udp(user_id="user", udp_id=service_id, process_graph={"p": 1})

    data = json.loads(path.read_text())
    assert service_id in data["services"]
    assert service_id in data["udp_definitions"]


def test_each_store_only_sees_its_namespace(tmp_path: Path):
    """Ensure services/UDPs are isolated when loaded."""
    path = tmp_path / "store.json"
    path.write_text(
        json.dumps(
            {
                "services": {"svc": {"user_id": "u1", "service": {"n": 1}}},
                "udp_definitions": {
                    "udp": {
                        "user_id": "u1",
                        "process_graph": {"p": 1},
                        "created_at": "2024-01-01T00:00:00Z",
                        "updated_at": "2024-01-01T00:00:00Z",
                    }
                },
            }
        )
    )

    service_store = get_store(str(path))
    udp_store = get_udp_store(str(path))

    assert service_store.get_service("svc") is not None
    assert service_store.get_service("udp") is None

    assert udp_store.get_udp(user_id="u1", udp_id="udp") is not None
    assert udp_store.get_udp(user_id="u1", udp_id="svc") is None
