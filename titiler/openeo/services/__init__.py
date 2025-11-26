"""titiler.openeo.services."""

import json
from urllib.parse import urlparse

from .base import ServicesStore, TileAssignmentStore, UdpStore


def _is_sqlalchemy_scheme(parsed) -> bool:
    """Helper to check if a URL should be treated as SQLAlchemy."""
    return bool(
        parsed.scheme == "sqlalchemy"
        or parsed.scheme.startswith("postgresql")
        or parsed.scheme.startswith("sqlite")
    )


def get_store(store_uri: str) -> ServicesStore:
    """Return Service Store."""
    parsed = urlparse(store_uri)

    if parsed.path.endswith(".json"):
        from .local import LocalStore  # noqa

        return LocalStore(json.load(open(store_uri)))  # type: ignore

    if parsed.path.endswith(".db"):
        from .duckdb import DuckDBStore  # noqa

        return DuckDBStore(store=store_uri)

    if _is_sqlalchemy_scheme(parsed):
        from .sqlalchemy import SQLAlchemyStore  # noqa

        return SQLAlchemyStore(store=store_uri)

    raise ValueError(f"Couldn't load {store_uri}")


def get_udp_store(store_uri: str) -> UdpStore:
    """Return UDP Store."""
    parsed = urlparse(store_uri)

    if parsed.path.endswith(".json"):
        from .local import LocalUdpStore  # noqa

        try:
            data = json.load(open(store_uri))
        except FileNotFoundError:
            data = {}
        return LocalUdpStore(store=data)  # type: ignore[arg-type]

    if parsed.path.endswith(".db") or parsed.scheme == "duckdb":
        from .duckdb import DuckDBUdpStore  # noqa

        return DuckDBUdpStore(store=store_uri)

    if _is_sqlalchemy_scheme(parsed):
        from .sqlalchemy import SQLAlchemyUdpStore  # noqa

        return SQLAlchemyUdpStore(store=store_uri)

    raise ValueError(f"Couldn't load UDP store {store_uri}")


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

    if _is_sqlalchemy_scheme(parsed):
        from .sqlalchemy_tile import SQLAlchemyTileStore  # noqa

        return SQLAlchemyTileStore(store=store_uri)

    raise ValueError(f"Tile store not supported for {store_uri}")
