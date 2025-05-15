"""Cache control middleware for titiler-openeo."""

from typing import Sequence

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from titiler.openeo.settings import ApiSettings


class DynamicCacheControlMiddleware:
    """Middleware to set Cache-Control headers based on endpoint type."""

    def __init__(
        self,
        app: ASGIApp,
        static_paths: Sequence[str] = ("/static/",),
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
            dynamic_paths: Paths that should use dynamic caching policy
        """
        self.app = app
        self.static_paths = static_paths
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
        if any(path.startswith(dynamic) for dynamic in self.dynamic_paths):
            return self.settings.cache_dynamic
        return self.settings.cache_default

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

        cache_header = self.get_cache_header(scope["path"])

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = dict(message["headers"])
                if b"cache-control" not in (h.lower() for h in headers.keys()):
                    message["headers"] = [
                        *message["headers"],
                        [b"cache-control", cache_header.encode()],
                    ]

            await send(message)

        await self.app(scope, receive, send_wrapper)
