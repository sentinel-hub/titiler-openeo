"""titiler.openeo.services duckDB base."""

import abc
from typing import Any, Dict, List

from attrs import define

from .base import ServicesStore


@define()
class DuckDBBaseStore(ServicesStore, metaclass=abc.ABCMeta):
    """Base class for DuckDB-based stores."""

    @abc.abstractmethod
    def _get_connection(self):
        """Get database connection."""
        ...

    @abc.abstractmethod
    def _get_table_query(self) -> str:
        """Get query to access services table."""
        ...

    def get_service(self, service_id: str) -> Dict | None:
        """Return a specific Service."""
        with self._get_connection() as con:
            result = con.execute(
                f"""
                SELECT service_id, service
                FROM {self._get_table_query()}
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
        with self._get_connection() as con:
            results = con.execute(
                f"""
                SELECT service_id, service
                FROM {self._get_table_query()}
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
        with self._get_connection() as con:
            results = con.execute(
                f"""
                SELECT service_id, service
                FROM {self._get_table_query()}
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

    @abc.abstractmethod
    def add_service(self, user_id: str, service: Dict, **kwargs) -> str:
        """Add Service."""
        ...

    @abc.abstractmethod
    def delete_service(self, service_id: str, **kwargs) -> bool:
        """Delete Service."""
        ...

    @abc.abstractmethod
    def update_service(
        self, user_id: str, item_id: str, val: Dict[str, Any], **kwargs
    ) -> str:
        """Update Service."""
        ...
