"""titiler.openeo.services duckDB."""

import uuid
from typing import Any, Dict

import duckdb
from attrs import define

from .duckdb_base import DuckDBBaseStore


@define()
class DuckDBStore(DuckDBBaseStore):
    """DuckDB Service Store using native DB format."""

    def __attrs_post_init__(self):
        """Post init: create table if not exists."""
        with duckdb.connect(self.store) as con:
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS services (
                    service_id VARCHAR PRIMARY KEY,
                    user_id VARCHAR,
                    service JSON
                );
                """
            )

    def _get_connection(self):
        """Get database connection."""
        return duckdb.connect(self.store)

    def _get_table_query(self) -> str:
        """Get query to access services table."""
        return "services"

    def add_service(self, user_id: str, service: Dict, **kwargs) -> str:
        """Add Service."""
        service_id = str(uuid.uuid4())
        with self._get_connection() as con:
            con.execute(
                """
                INSERT INTO services (service_id, user_id, service)
                VALUES (?, ?, ?)
                """,
                [service_id, user_id, service],
            )
        return service_id

    def delete_service(self, service_id: str, **kwargs) -> bool:
        """Delete Service."""
        with self._get_connection() as con:
            result = con.execute(
                """
                DELETE FROM services
                WHERE service_id = ?
                RETURNING service_id
                """,
                [service_id],
            ).fetchone()

            if not result:
                raise ValueError(f"Could not find service: {service_id}")

        return True

    def update_service(
        self, user_id: str, item_id: str, val: Dict[str, Any], **kwargs
    ) -> str:
        """Update Service."""
        with self._get_connection() as con:
            # Verify service exists and belongs to user
            result = con.execute(
                """
                SELECT user_id, service
                FROM services
                WHERE service_id = ?
                """,
                [item_id],
            ).fetchone()

            if not result:
                raise ValueError(f"Could not find service: {item_id}")

            if result[0] != user_id:
                raise ValueError(f"Service {item_id} does not belong to user {user_id}")

            # Merge the existing service with updates
            service = result[1]
            service.update(val)

            # Update service
            con.execute(
                """
                UPDATE services
                SET service = ?
                WHERE service_id = ?
                """,
                [service, item_id],
            )

        return item_id
