"""titiler.openeo.services."""

from urllib.parse import urlparse

from .base import ServicesStore  # noqa
from .duckdb import DuckDBStore  # noqa
from .local import LocalStore  # noqa
from .parquet import ParquetStore  # noqa


def get_store(store_uri: str) -> ServicesStore:
    """Return Service Store."""
    parsed = urlparse(store_uri)

    if parsed.path.endswith(".json"):
        import json

        return LocalStore(json.load(open(store_uri)))  # type: ignore

    if parsed.path.endswith(".db"):
        return DuckDBStore(store=store_uri)

    if parsed.path.endswith(".parquet"):
        return ParquetStore(store=store_uri)

    raise ValueError(f"Couldn't load {store_uri}")
