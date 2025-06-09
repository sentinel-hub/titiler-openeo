"""``pytest`` configuration."""

from pathlib import Path
from typing import Any, Literal, Union

import pytest
from fastapi import Header
from starlette.testclient import TestClient

from titiler.openeo.auth import Auth, User
from titiler.openeo.services.base import ServicesStore

StoreType = Literal["local", "duckdb", "sqlalchemy"]


@pytest.fixture(params=["local", "duckdb", "sqlalchemy"])
def store_type(request) -> StoreType:
    """Parameterize the service store type."""
    return request.param


@pytest.fixture
def store_path(tmp_path, store_type: StoreType) -> Union[Path, str]:
    """Create a temporary store path based on store type."""
    tmp_path.mkdir(exist_ok=True)
    if store_type == "local":
        path = tmp_path / "services.json"
        path.write_text("{}")
        return path
    elif store_type == "duckdb":
        path = tmp_path / "services.db"
        return path
    else:  # sqlalchemy in memory mock test
        return "sqlite:///:memory:"


@pytest.fixture
def app_with_auth(monkeypatch, store_path, store_type) -> TestClient:
    """Create App with authentication for testing."""
    monkeypatch.setenv("TITILER_OPENEO_STAC_API_URL", "https://stac.eoapi.dev")
    monkeypatch.setenv("TITILER_OPENEO_SERVICE_STORE_URL", f"{store_path}")

    from titiler.openeo.main import create_app
    from titiler.openeo.services import get_store

    app = create_app()

    # Get the store from the path and type
    store = get_store(f"{store_path}")

    # Override the auth dependency with the mock auth using the store
    mock_auth = MockAuth(store=store)
    app.dependency_overrides[app.endpoints.auth.validate] = mock_auth.validate

    return TestClient(app)


@pytest.fixture
def app_no_auth(monkeypatch, store_path, store_type) -> TestClient:
    """Create App without authentication for testing."""
    monkeypatch.setenv("TITILER_OPENEO_STAC_API_URL", "https://stac.eoapi.dev")
    monkeypatch.setenv("TITILER_OPENEO_SERVICE_STORE_URL", f"{store_path}")
    monkeypatch.setenv("TITILER_OPENEO_REQUIRE_AUTH", "false")

    from titiler.openeo.main import create_app

    return TestClient(create_app())


class MockAuth(Auth):
    """Mock authentication class for testing."""

    def __init__(self, store: ServicesStore):
        """Initialize auth with store."""
        self.store = store

    def login(self, authorization: str = Header(default=None)) -> Any:
        """Mock login method."""
        return {"access_token": "mock_token"}

    def validate(self, authorization: str = Header(default=None)) -> User:
        """Mock validate method."""
        return User(user_id="test_user")


@pytest.fixture
def clean_services(app_no_auth, store_path, store_type):
    """Ensure services are cleaned up after each test."""
    yield
    # Reset store to empty state
    if store_type == "local":
        store_path.write_text("{}")
    elif store_type == "duckdb":
        if store_path.exists():
            store_path.unlink()
    else:  # sqlalchemy in memory mock test
        pass
