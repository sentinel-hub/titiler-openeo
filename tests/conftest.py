"""``pytest`` configuration."""

import os

import pytest
from starlette.testclient import TestClient

DATA_DIR = os.path.join(os.path.dirname(__file__), "fixtures")


@pytest.fixture(autouse=True)
def app(monkeypatch):
    """Create App."""
    monkeypatch.setenv("TITILER_OPENEO_STAC_API_URL", "https://stac.eoapi.dev")
    monkeypatch.setenv("TITILER_OPENEO_SERVICE_STORE_URL", "services/eoapi.json")
    from titiler.openeo.main import app

    return app


@pytest.fixture
def client(app) -> TestClient:
    """Get test client."""
    return TestClient(app)
