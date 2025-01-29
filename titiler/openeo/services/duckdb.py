"""titiler.openeo.services duckDB."""

import uuid
from typing import Any, Dict, List, Optional

import duckdb
from attrs import define, field

from .base import ServicesStore


@define(kw_only=True, init=False)
class DuckDBStore(ServicesStore):
    """DuckDB Service Store."""

    store: str = field()

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

    def get_service(self, service_id: str) -> Optional[Dict]:
        """Return a specific Service."""
        with duckdb.connect(self.store) as con:
            result = con.execute(
                """
                SELECT service_id, service
                FROM services
                WHERE service_id = ?
                """,
                [service_id],
            ).fetchone()

            if not result:
                return None

            return {
                "id": result[0],
                **result[1],
            }

    def get_services(self, **kwargs) -> List[Dict]:
        """Return All Services."""
        with duckdb.connect(self.store) as con:
            results = con.execute(
                """
                SELECT service_id, service
                FROM services
                """
            ).fetchall()

            return [
                {
                    "id": result[0],
                    **result[1],
                }
                for result in results
            ]

    def get_user_services(self, user_id: str, **kwargs) -> List[Dict]:
        """Return List Services for a user."""
        with duckdb.connect(self.store) as con:
            results = con.execute(
                """
                SELECT service_id, service
                FROM services
                WHERE user_id = ?
                """,
                [user_id],
            ).fetchall()

            if not results:
                raise ValueError(f"Could not find service for user: {user_id}")

            return [
                {
                    "id": result[0],
                    **result[1],
                }
                for result in results
            ]

    def add_service(self, user_id: str, service: Dict, **kwargs) -> str:
        """Add Service."""
        service_id = str(uuid.uuid4())
        with duckdb.connect(self.store) as con:
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
        with duckdb.connect(self.store) as con:
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
        with duckdb.connect(self.store) as con:
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
