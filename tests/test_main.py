"""Test titiler.openeo.main.app."""

import importlib.metadata


def test_health(app_no_auth):
    """Test /healthz endpoint."""
    response = app_no_auth.get("/api")
    assert response.status_code == 200

    response = app_no_auth.get("/api.html")
    assert response.status_code == 200


def test_version():
    """Test that version can be accessed via importlib.metadata."""
    version = importlib.metadata.version("titiler-openeo")
    assert version == "0.8.0"

    # Also test that __version__ is available in the module
    from titiler.openeo import __version__
    assert __version__ == "0.8.0"
