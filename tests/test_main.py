"""Test titiler.openeo.main.app."""


def test_health(app_no_auth):
    """Test /healthz endpoint."""
    response = app_no_auth.get("/api")
    assert response.status_code == 200

    response = app_no_auth.get("/api.html")
    assert response.status_code == 200
