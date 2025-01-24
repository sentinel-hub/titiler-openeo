"""Test error handling."""

from fastapi import HTTPException

from titiler.openeo.errors import (
    AccessDenied,
    AuthenticationFailed,
    AuthenticationRequired,
    ProcessParameterMissing,
    ResourceNotFound,
    ServiceUnavailable,
)


def test_openeo_exception(app, client):
    """Test OpenEO exception handling."""

    @app.get("/test_openeo_error")
    def test_error():
        raise ProcessParameterMissing("test_param")

    response = client.get("/test_openeo_error")
    assert response.status_code == 400
    assert response.json() == {
        "code": "ProcessParameterMissing",
        "message": "Required process parameter 'test_param' is missing",
    }


def test_http_error(app, client):
    """Test HTTP error handling."""

    @app.get("/test_http_error")
    def test_error():
        raise HTTPException(status_code=404, detail="Resource not found")

    response = client.get("/test_http_error")
    assert response.status_code == 404
    assert response.json() == {
        "code": "InvalidRequest",
        "message": "Resource not found",
    }


def test_server_error(app, client):
    """Test server error handling."""

    @app.get("/test_server_error")
    def test_error():
        raise HTTPException(status_code=500, detail="Internal server error")

    response = client.get("/test_server_error")
    assert response.status_code == 500
    assert response.json() == {
        "code": "ServerError",
        "message": "Internal server error",
    }


def test_authentication_required(app, client):
    """Test authentication required error."""

    @app.get("/test_auth_required")
    def test_error():
        raise AuthenticationRequired()

    response = client.get("/test_auth_required")
    assert response.status_code == 401
    assert response.json() == {
        "code": "AuthenticationRequired",
        "message": "Authentication is required to access this resource",
    }


def test_authentication_failed(app, client):
    """Test authentication failed error."""

    @app.get("/test_auth_failed")
    def test_error():
        raise AuthenticationFailed()

    response = client.get("/test_auth_failed")
    assert response.status_code == 401
    assert response.json() == {
        "code": "AuthenticationFailed",
        "message": "The provided credentials are invalid",
    }


def test_access_denied(app, client):
    """Test access denied error."""

    @app.get("/test_access_denied")
    def test_error():
        raise AccessDenied()

    response = client.get("/test_access_denied")
    assert response.status_code == 403
    assert response.json() == {
        "code": "AccessDenied",
        "message": "You don't have permission to access this resource",
    }


def test_resource_not_found(app, client):
    """Test resource not found error."""

    @app.get("/test_not_found")
    def test_error():
        raise ResourceNotFound("collection", "test-123")

    response = client.get("/test_not_found")
    assert response.status_code == 404
    assert response.json() == {
        "code": "ResourceNotFound",
        "message": "The requested collection with id 'test-123' does not exist",
    }


def test_service_unavailable(app, client):
    """Test service unavailable error."""

    @app.get("/test_unavailable")
    def test_error():
        raise ServiceUnavailable("The service is under maintenance")

    response = client.get("/test_unavailable")
    assert response.status_code == 503
    assert response.json() == {
        "code": "ServiceUnavailable",
        "message": "The service is under maintenance",
    }
