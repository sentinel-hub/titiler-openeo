"""Test titiler.openeo.main.app."""


def test_health(app):
    """Test /healthz endpoint."""
    response = app.get("/api")
    assert response.status_code == 200

    response = app.get("/api.html")
    assert response.status_code == 200
