"""PgSTAC backend."""

from contextlib import asynccontextmanager
from typing import Dict, List

from attrs import define, field
from fastapi import FastAPI
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from ..settings import PgSTACSettings
from .base import BaseBackend


@define
class pgStacBackend(BaseBackend):
    """PgSTAC Backend."""

    url: str = field(converter=str)

    # Connection POOL to the database
    pool: ConnectionPool = field(init=False)

    def get_collections(self, **kwargs) -> List[Dict]:
        """Return List of STAC Collections."""
        with self.pool.connection() as conn:
            with conn.cursor(row_factory=dict_row) as cursor:
                cursor.execute("SELECT * FROM pgstac.all_collections();")
                r = cursor.fetchone()

        return r.get("all_collections", [])

    def get_collection(self, collection_id: str, **kwargs) -> Dict:
        """Return STAC Collection"""
        with self.pool.connection() as conn:
            with conn.cursor(row_factory=dict_row) as cursor:
                cursor.execute(
                    "SELECT * FROM get_collection(%s);",
                    (collection_id,),
                )
                r = cursor.fetchone()

        return r.get("get_collection") or {}

    def get_items(self, collection_id: str, **kwargs) -> List[Dict]:
        """Return List of STAC Items."""
        return []

    def get_lifespan(self):
        """pgstac lifespan function."""

        @asynccontextmanager
        async def lifespan(app: FastAPI):
            """init ConnectionPool."""
            settings = PgSTACSettings()

            pool_kwargs = {
                "options": "-c search_path=pgstac,public -c application_name=pgstac",
            }
            self.pool = ConnectionPool(
                conninfo=self.url,
                min_size=settings.db_min_conn_size,
                max_size=settings.db_max_conn_size,
                max_waiting=settings.db_max_queries,
                max_idle=settings.db_max_idle,
                num_workers=settings.db_num_workers,
                kwargs=pool_kwargs,
                open=True,
            )
            self.pool.wait()
            yield
            self.pool.close()

        return lifespan
