"""``pytest`` configuration."""

import os

import pytest
from starlette.testclient import TestClient

DATA_DIR = os.path.join(os.path.dirname(__file__), "fixtures")


@pytest.fixture(autouse=True)
def app() -> TestClient:
    """Create App."""
    from titiler.openeo.main import app

    return TestClient(app)
