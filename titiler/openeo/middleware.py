"""Cache control middleware for titiler-openeo."""

from typing import Sequence

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .settings import ApiSettings


class DynamicCacheControlMiddleware:
    """Middleware to set Cache-Control headers based on endpoint type."""

    def __init__(
        self,
        app: ASGIApp,
        static_paths: Sequence[str] = ("/static/",),
        tile_paths: Sequence[str] = ("/services/xyz/",),
        dynamic_paths: Sequence[str] = (
            "/processes/",
            "/jobs/",
            "/collections/",
            "/services/",
            "/results/",
        ),
    ) -> None:
        """Initialize middleware.

        Args:
            app: The ASGI application
            static_paths: Paths that should use static caching policy
            tile_paths: Paths for tile endpoints that can be cached by browsers
            dynamic_paths: Paths that should use dynamic caching policy
        """
        self.app = app
        self.static_paths = static_paths
        self.tile_paths = tile_paths
        self.dynamic_paths = dynamic_paths
        self.settings = ApiSettings()

    def get_cache_header(self, path: str) -> str:
        """Get appropriate cache control header based on request path.

        Args:
            path: The request path

        Returns:
            The cache control header value
        """
        if any(path.startswith(static) for static in self.static_paths):
            return self.settings.cache_static
        # Check tile paths before general dynamic paths (more specific match first)
        if any(path.startswith(tile) for tile in self.tile_paths):
            return self.settings.cache_tiles
        if any(path.startswith(dynamic) for dynamic in self.dynamic_paths):
            return self.settings.cache_dynamic
        return self.settings.cache_default

    def get_status_cache_header(
        self, status: int, path_header: str, is_tile: bool
    ) -> str:
        """Get the cache control header for a response status.

        Only successful responses get the path-based policy. A tile 400/404
        (zoom out of range, no data) does not depend on the caller and gets a
        short private policy. Every other error (for example a 401 on a
        private tile, or a 5xx) is never stored.
        """
        if 200 <= status < 300:
            return path_header
        if is_tile and status in (400, 404):
            return self.settings.cache_tile_errors
        return "no-store"

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Process request/response.

        Args:
            scope: The connection scope
            receive: The receive channel
            send: The send channel
        """
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # Get the path relative to the application root
        # This handles deployments with a URL prefix (e.g., /openeo)
        path = scope["path"]
        root_path = scope.get("root_path", "")
        if root_path and path.startswith(root_path):
            path = path[len(root_path) :] or "/"

        cache_header = self.get_cache_header(path)
        is_tile = any(path.startswith(tile) for tile in self.tile_paths)

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = dict(message["headers"])
                if b"cache-control" not in (h.lower() for h in headers.keys()):
                    header = self.get_status_cache_header(
                        message["status"], cache_header, is_tile
                    )
                    message["headers"] = [
                        *message["headers"],
                        [b"cache-control", header.encode()],
                    ]

            await send(message)

        await self.app(scope, receive, send_wrapper)
