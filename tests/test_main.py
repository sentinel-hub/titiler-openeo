"""Test titiler.openeo.main.app."""

from fastapi.testclient import TestClient


def test_health(app):
    """Test /healthz endpoint."""
    with TestClient(app) as client:
        response = client.get("/api")
        assert response.status_code == 200

        response = client.get("/api.html")
        assert response.status_code == 200
