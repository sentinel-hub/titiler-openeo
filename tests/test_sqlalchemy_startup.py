"""Startup of the app while PostgreSQL is down (#479)."""

import os
import subprocess
import sys

import pytest
from starlette.testclient import TestClient

from titiler.openeo.services.sqlalchemy import SQLAlchemyStore

# Nothing listens on port 1, so connecting fails at once.
DOWN = "postgresql://u:p@127.0.0.1:1/db"


def test_app_imports_with_postgres_down(tmp_path):
    """Importing the app does not connect to the database."""
    pytest.importorskip("psycopg2")
    env = {
        **os.environ,
        "TITILER_OPENEO_STAC_API_URL": "https://stac.example.com",
        "TITILER_OPENEO_STORE_URL": DOWN,
        "TITILER_OPENEO_TILE_STORE_URL": DOWN,
    }

    # In `tmp_path`, so that no `.env` of the repository applies.
    result = subprocess.run(
        [sys.executable, "-c", "import titiler.openeo.main"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert result.returncode == 0, result.stderr


# Overrides the `store_type` parametrization of `main_module`.
@pytest.mark.parametrize("store_type", ["sqlalchemy"])
def test_readyz_reports_database_down(main_module, monkeypatch):
    """With the database down, the pod stays live but gets no traffic."""
    pytest.importorskip("psycopg2")
    from titiler.openeo.stacapi import stacApiBackend

    monkeypatch.setattr(stacApiBackend, "ping", lambda self, *a, **kw: None)
    monkeypatch.setattr(main_module, "service_store", SQLAlchemyStore(store=DOWN))
    client = TestClient(main_module.create_app())

    response = client.get("/readyz")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "degraded"
    assert body["checks"]["store"]["status"] == "error"

    assert client.get("/healthz").status_code == 200
