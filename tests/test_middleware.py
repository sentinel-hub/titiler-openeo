"""Tests for the cache control middleware."""

import pytest
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from titiler.openeo.middleware import DynamicCacheControlMiddleware


def homepage(request):
    return PlainTextResponse("Hello")


def static_file(request):
    return PlainTextResponse("Static content")


def tile_endpoint(request):
    return PlainTextResponse("Tile data")


def services_endpoint(request):
    return PlainTextResponse("Services list")


def collections_endpoint(request):
    return PlainTextResponse("Collections")


@pytest.fixture
def app():
    """Create a test app with the middleware."""
    routes = [
        Route("/", homepage),
        Route("/static/file.js", static_file),
        Route("/services/xyz/123/tiles/0/0/0", tile_endpoint),
        Route("/services/", services_endpoint),
        Route("/collections/", collections_endpoint),
    ]
    app = Starlette(routes=routes)
    app.add_middleware(DynamicCacheControlMiddleware)
    return app


@pytest.fixture
def client(app):
    """Create a test client."""
    return TestClient(app)


class TestCacheControlMiddleware:
    """Tests for DynamicCacheControlMiddleware."""

    def test_static_path_caching(self, client):
        """Static paths should get static caching headers."""
        response = client.get("/static/file.js")
        assert response.status_code == 200
        assert "cache-control" in response.headers
        assert response.headers["cache-control"] == "public, max-age=3600"

    def test_tile_path_caching(self, client):
        """Tile paths should get tile caching headers."""
        response = client.get("/services/xyz/123/tiles/0/0/0")
        assert response.status_code == 200
        assert "cache-control" in response.headers
        assert response.headers["cache-control"] == "public, max-age=3600"

    def test_dynamic_path_caching(self, client):
        """Dynamic paths should get no-cache headers."""
        response = client.get("/services/")
        assert response.status_code == 200
        assert "cache-control" in response.headers
        assert response.headers["cache-control"] == "no-cache"

    def test_collections_path_caching(self, client):
        """Collections path should get dynamic caching headers."""
        response = client.get("/collections/")
        assert response.status_code == 200
        assert "cache-control" in response.headers
        assert response.headers["cache-control"] == "no-cache"

    def test_default_path_caching(self, client):
        """Unmatched paths should get default caching headers."""
        response = client.get("/")
        assert response.status_code == 200
        assert "cache-control" in response.headers
        assert response.headers["cache-control"] == "no-store"

    def test_tile_takes_precedence_over_services(self, client):
        """Tile paths should match before general /services/ path."""
        response = client.get("/services/xyz/123/tiles/0/0/0")
        # Should be tile caching, not dynamic (services) caching
        assert response.headers["cache-control"] == "public, max-age=3600"


class TestCacheControlMiddlewareWithRootPath:
    """Tests for middleware with root_path prefix (e.g., /openeo)."""

    @pytest.fixture
    def app_with_prefix(self):
        """Create a test app with routes that simulate a prefix deployment."""
        routes = [
            Route("/", homepage),
            Route("/static/file.js", static_file),
            Route("/services/xyz/123/tiles/0/0/0", tile_endpoint),
            Route("/services/", services_endpoint),
            Route("/collections/", collections_endpoint),
        ]
        app = Starlette(routes=routes)
        app.add_middleware(DynamicCacheControlMiddleware)
        return app

    @pytest.fixture
    def client_with_prefix(self, app_with_prefix):
        """Create a test client with root_path set."""
        return TestClient(app_with_prefix, root_path="/openeo")

    def test_tile_path_with_prefix(self, client_with_prefix):
        """Tile paths should work correctly with root_path prefix."""
        response = client_with_prefix.get("/services/xyz/123/tiles/0/0/0")
        assert response.status_code == 200
        assert "cache-control" in response.headers
        # Should still match tile path after stripping prefix
        assert response.headers["cache-control"] == "public, max-age=3600"

    def test_static_path_with_prefix(self, client_with_prefix):
        """Static paths should work correctly with root_path prefix."""
        response = client_with_prefix.get("/static/file.js")
        assert response.status_code == 200
        assert response.headers["cache-control"] == "public, max-age=3600"

    def test_dynamic_path_with_prefix(self, client_with_prefix):
        """Dynamic paths should work correctly with root_path prefix."""
        response = client_with_prefix.get("/services/")
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-cache"

    def test_default_path_with_prefix(self, client_with_prefix):
        """Default paths should work correctly with root_path prefix."""
        response = client_with_prefix.get("/")
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"


class TestGetCacheHeader:
    """Unit tests for the get_cache_header method."""

    @pytest.fixture
    def middleware(self):
        """Create a middleware instance for testing."""

        async def dummy_app(scope, receive, send):
            pass

        return DynamicCacheControlMiddleware(dummy_app)

    def test_static_path(self, middleware):
        """Static paths should return static cache header."""
        assert middleware.get_cache_header("/static/foo.js") == "public, max-age=3600"

    def test_tile_path(self, middleware):
        """Tile paths should return tile cache header."""
        assert (
            middleware.get_cache_header("/services/xyz/abc/tiles/1/2/3")
            == "public, max-age=3600"
        )

    def test_services_path(self, middleware):
        """Services path should return dynamic cache header."""
        assert middleware.get_cache_header("/services/") == "no-cache"
        assert middleware.get_cache_header("/services/abc") == "no-cache"

    def test_collections_path(self, middleware):
        """Collections path should return dynamic cache header."""
        assert middleware.get_cache_header("/collections/") == "no-cache"

    def test_processes_path(self, middleware):
        """Processes path should return dynamic cache header."""
        assert middleware.get_cache_header("/processes/") == "no-cache"

    def test_jobs_path(self, middleware):
        """Jobs path should return dynamic cache header."""
        assert middleware.get_cache_header("/jobs/") == "no-cache"

    def test_results_path(self, middleware):
        """Results path should return dynamic cache header."""
        assert middleware.get_cache_header("/results/") == "no-cache"

    def test_unknown_path(self, middleware):
        """Unknown paths should return default cache header."""
        assert middleware.get_cache_header("/unknown") == "no-store"
        assert middleware.get_cache_header("/") == "no-store"

    def test_tile_path_precedence(self, middleware):
        """Tile path should take precedence over general services path."""
        # /services/xyz/ should match tile_paths, not dynamic_paths
        result = middleware.get_cache_header("/services/xyz/123/tiles/0/0/0")
        assert result == "public, max-age=3600"
