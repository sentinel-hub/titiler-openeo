"""titiler.openeo.services."""

from urllib.parse import urlparse

from .base import ServicesStore  # noqa


def get_store(store_uri: str) -> ServicesStore:
    """Return Service Store."""
    parsed = urlparse(store_uri)

    if parsed.path.endswith(".json"):
        import json

        from .local import LocalStore

        return LocalStore(json.load(open(store_uri)))  # type: ignore

    raise ValueError(f"Couldn't load {store_uri}")
