"""Test CORS headers on normal and error responses (issue #368)."""

from fastapi.testclient import TestClient

ORIGIN = "https://jupytergis.example.com"


def _client(app_no_auth: TestClient) -> TestClient:
    """Return a client that does not raise server errors, with a failing route."""
    app = app_no_auth.app

    def boom():
        raise RuntimeError("boom")

    app.add_api_route("/test-boom", boom)
    return TestClient(app, raise_server_exceptions=False)


def test_cors_header_on_normal_response(app_no_auth):
    """A wildcard origin deployment sends `Access-Control-Allow-Origin`."""
    response = app_no_auth.get("/", headers={"Origin": ORIGIN})
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "*"


def test_cors_header_on_unhandled_exception(app_no_auth):
    """An unhandled exception gives an openEO error body with CORS headers."""
    client = _client(app_no_auth)
    response = client.get("/test-boom", headers={"Origin": ORIGIN})
    assert response.status_code == 500
    assert response.headers["access-control-allow-origin"] == "*"
    assert response.json()["code"] == "Internal"


def test_cors_header_on_openeo_error(app_no_auth):
    """An openEO error response (an unknown service) has CORS headers."""
    response = app_no_auth.get("/services/does-not-exist", headers={"Origin": ORIGIN})
    assert response.status_code >= 400
    assert response.headers["access-control-allow-origin"] == "*"
