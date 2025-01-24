"""Test titiler.openeo.main.app."""


def test_health(app, client):
    """Test /healthz endpoint."""
    response = client.get("/api")
    assert response.status_code == 200

    response = client.get("/api.html")
    assert response.status_code == 200
