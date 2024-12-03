"""``pytest`` configuration."""

import os

import pytest
from starlette.testclient import TestClient

DATA_DIR = os.path.join(os.path.dirname(__file__), "fixtures")


@pytest.fixture(autouse=True)
def app(monkeypatch) -> TestClient:
    """Create App."""
    monkeypatch.setenv(
        "TITILER_STACAPI_STAC_API_URL", "https://stac.dataspace.copernicus.eu/v1"
    )

    from titiler.openeo.main import app

    return TestClient(app)
