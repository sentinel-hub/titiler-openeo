"""Create the SQL stores' tables: `python -m titiler.openeo.services.create_schema`.

Run it before the first start and after each upgrade; the Helm chart runs it
in a Job after each install and upgrade. It reads the store URLs the app reads
and creates the missing tables in each SQL database. Any error exits non-zero.
"""

import sys
from typing import Optional
from urllib.parse import urlparse

from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url

from . import _is_sqlalchemy_scheme
from .sqlalchemy import _get_engine, create_schema


class _StoreUrls(BaseSettings):
    """The store URLs of `BackendSettings`, without its other required fields."""

    store_url: Optional[str] = None
    tile_store_url: Optional[str] = None

    model_config = SettingsConfigDict(
        env_prefix="TITILER_OPENEO_",
        env_file=".env",
        extra="ignore",
    )


def main() -> None:
    urls = _StoreUrls()
    # A JSON or DuckDB services store has no tables, but its tile store can be
    # on a SQL database.
    databases = [
        url
        for url in dict.fromkeys([urls.store_url, urls.tile_store_url])
        if url and _is_sqlalchemy_scheme(urlparse(url))
    ]
    if not databases:
        sys.exit(
            "No SQL database in TITILER_OPENEO_STORE_URL or "
            "TITILER_OPENEO_TILE_STORE_URL"
        )

    for url in databases:
        create_schema(_get_engine(url))
        print(f"Tables ready in {make_url(url).render_as_string(hide_password=True)}")


if __name__ == "__main__":
    main()
