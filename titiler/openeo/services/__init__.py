"""titiler.openeo.services."""

from urllib.parse import urlparse

from .base import ServicesStore  # noqa


def get_store(store_uri: str) -> ServicesStore:
    """Return Service Store."""
    parsed = urlparse(store_uri)

    if parsed.path.endswith(".json"):
        import json

        from .local import LocalStore  # noqa

        return LocalStore(json.load(open(store_uri)))  # type: ignore

    if parsed.path.endswith(".db"):
        from .duckdb import DuckDBStore  # noqa

        return DuckDBStore(store=store_uri)

    if parsed.scheme == "sqlalchemy":
        from .sqlalchemy import SQLAlchemyStore  # noqa

        return SQLAlchemyStore(store=store_uri)

    raise ValueError(f"Couldn't load {store_uri}")
