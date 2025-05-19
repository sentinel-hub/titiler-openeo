"""Test HTTP error codes for tile assignment endpoints."""

from base64 import b64encode
import json

import pytest
from fastapi.testclient import TestClient

# Base service template
BASE_SERVICE = {
    "type": "XYZ",
    "configuration": {
        "scope": "restricted",
        "tile_store": True,
        "tile_size": 256,
        "minzoom": 7,
        "maxzoom": 7,
        "inject_user": True,
    },
    "enabled": True,
    "process": {
        "parameters": [],
        "process_graph": {
            "assign": {
                "process_id": "tile_assignment",
                "arguments": {
                    "zoom": 7,
                    "x_range": [0, 1],  # 2 tiles horizontally
                    "y_range": [0, 1],  # 2 tiles vertically
                    "store": {"from_parameter": "_openeo_tile_store"},
                    "user_id": {"from_parameter": "_openeo_user"},
                    "service_id": "test-service",
                },
                "result": False,
            },
            "save": {
                "process_id": "save_result",
                "arguments": {"data": {"from_node": "assign"}, "format": "txt"},
                "result": True,
            },
        },
    },
}

USERS_CONFIG = {
    "user1": {"password": "pass1", "roles": ["user"], "scope": "restricted"},
    "user2": {"password": "pass2", "roles": ["user"], "scope": "restricted"},
    "user3": {"password": "pass3", "roles": ["user"], "scope": "restricted"},
    "user4": {"password": "pass4", "roles": ["user"], "scope": "restricted"},
    "user5": {"password": "pass5", "roles": ["user"], "scope": "restricted"},
}


def get_user_token(username: str) -> str:
    """Get a basic auth token for a user."""
    user = USERS_CONFIG[username]
    return "basic//" + b64encode(f"{username}:{user['password']}".encode()).decode(
        "utf-8"
    )


def get_service_config(service_id: str, title: str, stage: str = None) -> dict:
    """Get service configuration."""
    service = BASE_SERVICE.copy()
    service["id"] = service_id
    service["title"] = title
    if stage:
        service["process"]["process_graph"]["assign"]["arguments"]["stage"] = stage
    return service


def create_test_services(app, auth_token: str) -> dict:
    """Create test services and return mapping of service names to their IDs."""
    services = [
        ("test-service", "Test Tile Assignment Service", "claim"),
        ("test-release", "Test Release Service", "release"),
        ("test-submit", "Test Submit Service", "submit"),
    ]

    service_ids = {}
    for args in services:
        service = get_service_config(*args)
        response = app.post(
            "/services", json=service, headers={"Authorization": auth_token}
        )
        assert response.status_code == 201
        # Extract service ID from location header
        location = response.headers["Location"]
        service_ids[args[0]] = location.split("/")[-1]  # Get last part of URL path

    return service_ids


@pytest.fixture
def engine():
    """Create a shared database engine."""
    from sqlalchemy import create_engine
    return create_engine("sqlite:///file::memory:?cache=shared&mode=memory")

@pytest.fixture(autouse=True)
def clear_db(engine):
    """Clear database tables before each test."""
    from titiler.openeo.services.sqlalchemy import Base
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield

@pytest.fixture
def app_with_mock_store(monkeypatch, engine, clear_db):
    """Create App with authentication for testing."""
    monkeypatch.setenv(
        "TITILER_OPENEO_TILE_STORE_URL",
        "sqlite:///file::memory:?cache=shared&mode=memory",
    )
    monkeypatch.setenv("TITILER_OPENEO_STAC_API_URL", "https://stac.eoapi.dev")
    monkeypatch.setenv(
        "TITILER_OPENEO_SERVICE_STORE_URL",
        "sqlite:///file::memory:?cache=shared&mode=memory",
    )
    monkeypatch.setenv("TITILER_OPENEO_AUTH_METHOD", "basic")
    monkeypatch.setenv("TITILER_OPENEO_AUTH_USERS", json.dumps(USERS_CONFIG))

    from titiler.openeo.main import create_app

    app = create_app()

    client = TestClient(app, raise_server_exceptions=False)

    # Create test services and store service IDs
    service_ids = create_test_services(client, get_user_token("user1"))

    # Attach service IDs to client for test access
    client.service_ids = service_ids

    return client


def test_claim_tile_unauthorized(app_with_mock_store):
    """Test claiming tile without authentication."""
    service_id = app_with_mock_store.service_ids["test-service"]
    response = app_with_mock_store.get(f"/services/xyz/{service_id}/tiles/7/0/0")
    assert response.status_code == 401
    assert "Authentication required" in response.json()["message"]


def test_claim_tile_already_has_one(app_with_mock_store):
    """Test claiming tile when user already has one."""
    service_id = app_with_mock_store.service_ids["test-service"]
    # First claim succeeds
    response = app_with_mock_store.get(
        f"/services/xyz/{service_id}/tiles/7/0/0",
        headers={"Authorization": get_user_token("user1")},
    )
    assert response.status_code == 200

    # Second claim fails with 200 again
    response = app_with_mock_store.get(
        f"/services/xyz/{service_id}/tiles/7/0/0",
        headers={"Authorization": get_user_token("user1")},
    )
    assert response.status_code == 200


def test_claim_tile_no_available(app_with_mock_store):
    """Test claiming tile when none are available."""
    service_id = app_with_mock_store.service_ids["test-service"]

    # First 4 users claim all available tiles
    for i in range(1, 5):
        response = app_with_mock_store.get(
            f"/services/xyz/{service_id}/tiles/7/0/0",
            headers={"Authorization": get_user_token(f"user{i}")},
        )
        assert response.status_code == 200

    # Fifth user tries to claim a tile but none are available
    response = app_with_mock_store.get(
        f"/services/xyz/{service_id}/tiles/7/0/0",
        headers={"Authorization": get_user_token("user5")},
    )
    assert response.status_code == 409
    assert "No tile available" in response.json()["message"]


def test_release_tile_unauthorized(app_with_mock_store):
    """Test releasing tile without authentication."""
    service_id = app_with_mock_store.service_ids["test-release"]
    response = app_with_mock_store.get(f"/services/xyz/{service_id}/tiles/7/0/0")
    assert response.status_code == 401
    assert "Authentication required" in response.json()["message"]


def test_release_tile_not_assigned(app_with_mock_store):
    """Test releasing tile that isn't assigned."""
    service_id = app_with_mock_store.service_ids["test-release"]
    response = app_with_mock_store.get(
        f"/services/xyz/{service_id}/tiles/7/0/0",
        headers={"Authorization": get_user_token("user1")},
    )
    assert response.status_code == 404
    assert "No tile assigned" in response.json()["message"]


def test_release_submitted_tile(app_with_mock_store):
    """Test releasing a submitted tile."""
    claim_service_id = app_with_mock_store.service_ids["test-service"]
    submit_service_id = app_with_mock_store.service_ids["test-submit"]
    release_service_id = app_with_mock_store.service_ids["test-release"]

    # Claim and submit a tile
    app_with_mock_store.get(
        f"/services/xyz/{claim_service_id}/tiles/7/0/0",
        headers={"Authorization": get_user_token("user1")},
    )
    app_with_mock_store.get(
        f"/services/xyz/{submit_service_id}/tiles/7/0/0",
        headers={"Authorization": get_user_token("user1")},
    )

    # Try to release it
    response = app_with_mock_store.get(
        f"/services/xyz/{release_service_id}/tiles/7/0/0",
        headers={"Authorization": get_user_token("user1")},
    )
    assert response.status_code == 409
    assert "already locked" in response.json()["message"]


def test_submit_tile_unauthorized(app_with_mock_store):
    """Test submitting tile without authentication."""
    service_id = app_with_mock_store.service_ids["test-submit"]
    response = app_with_mock_store.get(f"/services/xyz/{service_id}/tiles/7/0/0")
    assert response.status_code == 401
    assert "Authentication required" in response.json()["message"]


def test_submit_tile_not_assigned(app_with_mock_store):
    """Test submitting tile that isn't assigned."""
    service_id = app_with_mock_store.service_ids["test-submit"]
    response = app_with_mock_store.get(
        f"/services/xyz/{service_id}/tiles/7/0/0",
        headers={"Authorization": get_user_token("user1")},
    )
    assert response.status_code == 404
    assert "No tile assigned" in response.json()["message"]


def test_submit_already_submitted_tile(app_with_mock_store):
    """Test submitting an already submitted tile."""
    claim_service_id = app_with_mock_store.service_ids["test-service"]
    submit_service_id = app_with_mock_store.service_ids["test-submit"]

    # Claim and submit a tile
    app_with_mock_store.get(
        f"/services/xyz/{claim_service_id}/tiles/7/0/0",
        headers={"Authorization": get_user_token("user1")},
    )
    app_with_mock_store.get(
        f"/services/xyz/{submit_service_id}/tiles/7/0/0",
        headers={"Authorization": get_user_token("user1")},
    )

    # Try to submit it again
    response = app_with_mock_store.get(
        f"/services/xyz/{submit_service_id}/tiles/7/0/0",
        headers={"Authorization": get_user_token("user1")},
    )
    assert response.status_code == 409
    assert "already locked" in response.json()["message"].lower()
