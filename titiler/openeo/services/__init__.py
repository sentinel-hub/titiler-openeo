"""titiler.openeo.services."""

from urllib.parse import urlparse

from .base import ServicesStore, TileAssignmentStore  # noqa
from .sqlalchemy_tile import SQLAlchemyTileStore  # noqa


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

    if (
        parsed.scheme == "sqlalchemy"
        or parsed.scheme.startswith("postgresql")
        or parsed.scheme.startswith("sqlite")
    ):
        from .sqlalchemy import SQLAlchemyStore  # noqa

        return SQLAlchemyStore(store=store_uri)

    raise ValueError(f"Couldn't load {store_uri}")


def get_tile_store(store_uri: str) -> TileAssignmentStore:
    """Return Tile Assignment Store.

    Args:
        store_uri: URI for the store

    Returns:
        TileAssignmentStore implementation

    Raises:
        ValueError: When store type is not supported
    """
    parsed = urlparse(store_uri)

    if (
        parsed.scheme == "sqlalchemy"
        or parsed.scheme.startswith("postgresql")
        or parsed.scheme.startswith("sqlite")
    ):
        return SQLAlchemyTileStore(store=store_uri)

    raise ValueError(f"Tile store not supported for {store_uri}")
