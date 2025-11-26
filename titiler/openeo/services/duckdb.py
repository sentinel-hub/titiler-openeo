"""titiler.openeo.services duckDB."""

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import duckdb
from attrs import define, field

from ..models.auth import User
from .base import ServicesStore, UdpStore


@define(kw_only=True)
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
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS user_tracking (
                    user_id VARCHAR,
                    provider VARCHAR,
                    first_login TIMESTAMP,
                    last_login TIMESTAMP,
                    login_count INTEGER,
                    email VARCHAR,
                    name VARCHAR,
                    PRIMARY KEY (user_id, provider)
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
                **json.loads(result[1]),
            }

    def get_services(self, **kwargs) -> List[Dict]:
        """Return All Services."""
        with duckdb.connect(self.store) as con:
            results = con.execute(
                """
                SELECT service_id, json(service)
                FROM services
                """
            ).fetchall()

            return [
                {
                    "id": result[0],
                    **json.loads(result[1]),
                }
                for result in results
            ]

    def get_user_services(self, user_id: str, **kwargs) -> List[Dict]:
        """Return List Services for a user."""
        with duckdb.connect(self.store) as con:
            results = con.execute(
                """
                SELECT service_id, service::JSON
                FROM services
                WHERE user_id = ?
                """,
                [user_id],
            ).fetchall()

            return [
                {
                    "id": result[0],
                    **json.loads(result[1]),
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

    def track_user_login(self, user: User, provider: str) -> None:
        """Track user login activity."""
        now = datetime.now(timezone.utc)

        with duckdb.connect(self.store) as con:
            # Begin transaction for atomic operation
            con.execute("BEGIN TRANSACTION")
            try:
                # Check if record exists
                exists = con.execute(
                    """
                    SELECT COUNT(*)
                    FROM user_tracking
                    WHERE user_id = ? AND provider = ?
                    """,
                    [user.user_id, provider],
                ).fetchone()[0]

                if exists:
                    # Update existing record
                    con.execute(
                        """
                        UPDATE user_tracking
                        SET last_login = ?,
                            login_count = login_count + 1,
                            email = ?,
                            name = ?
                        WHERE user_id = ? AND provider = ?
                        """,
                        [now, user.email, user.name, user.user_id, provider],
                    )
                else:
                    # Insert new record if update affected no rows
                    con.execute(
                        """
                        INSERT INTO user_tracking
                        (user_id, provider, first_login, last_login, login_count, email, name)
                        VALUES (?, ?, ?, ?, 1, ?, ?)
                        """,
                        [user.user_id, provider, now, now, user.email, user.name],
                    )
                con.execute("COMMIT")
            except Exception:
                con.execute("ROLLBACK")
                raise

    def get_user_tracking(
        self, user_id: str, provider: str
    ) -> Optional[Dict[str, Any]]:
        """Get user tracking information."""
        with duckdb.connect(self.store) as con:
            result = con.execute(
                """
                SELECT user_id, provider, first_login, last_login,
                       login_count, email, name
                FROM user_tracking
                WHERE user_id = ? AND provider = ?
                """,
                [user_id, provider],
            ).fetchone()

            if not result:
                return None

            return {
                "user_id": result[0],
                "provider": result[1],
                "first_login": result[2],
                "last_login": result[3],
                "login_count": result[4],
                "email": result[5],
                "name": result[6],
            }


def _serialize_json(value: Optional[Dict[str, Any]]) -> Optional[str]:
    """Serialize JSON-like values for duckdb storage."""
    if value is None:
        return None
    return json.dumps(value)


def _deserialize_json(value: Optional[Any]) -> Optional[Dict[str, Any]]:
    """Deserialize JSON values read from duckdb."""
    if value is None:
        return None
    if isinstance(value, str):
        return json.loads(value)
    return value


@define(kw_only=True)
class DuckDBUdpStore(UdpStore):
    """DuckDB UDP Store."""

    store: str = field()

    def __attrs_post_init__(self):
        """Post init: create UDP table if not exists."""
        with duckdb.connect(self.store) as con:
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS udp_definitions (
                    id VARCHAR PRIMARY KEY,
                    user_id VARCHAR NOT NULL,
                    process_graph JSON NOT NULL,
                    parameters JSON,
                    metadata JSON,
                    created_at TIMESTAMP NOT NULL,
                    updated_at TIMESTAMP NOT NULL
                );
                """
            )

    def list_udps(
        self, user_id: str, limit: int = 100, offset: int = 0
    ) -> List[Dict[str, Any]]:
        """List UDPs for a user."""
        with duckdb.connect(self.store) as con:
            results = con.execute(
                """
                SELECT id,
                       user_id,
                       process_graph,
                       parameters,
                       metadata,
                       created_at,
                       updated_at
                FROM udp_definitions
                WHERE user_id = ?
                ORDER BY created_at DESC
                LIMIT ?
                OFFSET ?
                """,
                [user_id, limit, offset],
            ).fetchall()

            return [self._row_to_dict(row) for row in results]

    def get_udp(self, user_id: str, udp_id: str) -> Optional[Dict[str, Any]]:
        """Get a single UDP for a user."""
        with duckdb.connect(self.store) as con:
            result = con.execute(
                """
                SELECT id,
                       user_id,
                       process_graph,
                       parameters,
                       metadata,
                       created_at,
                       updated_at
                FROM udp_definitions
                WHERE id = ? AND user_id = ?
                """,
                [udp_id, user_id],
            ).fetchone()

            if result is None:
                return None

            return self._row_to_dict(result)

    def upsert_udp(
        self,
        user_id: str,
        udp_id: str,
        process_graph: Dict[str, Any],
        parameters: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Create or replace a UDP for a user."""
        now = datetime.utcnow()
        with duckdb.connect(self.store) as con:
            existing = con.execute(
                """
                SELECT user_id
                FROM udp_definitions
                WHERE id = ?
                """,
                [udp_id],
            ).fetchone()

            if existing is not None and existing[0] != user_id:
                raise ValueError(f"UDP {udp_id} does not belong to user {user_id}")

            if existing is not None:
                con.execute(
                    """
                    UPDATE udp_definitions
                    SET process_graph = ?,
                        parameters = ?,
                        metadata = ?,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    [
                        _serialize_json(process_graph),
                        _serialize_json(parameters),
                        _serialize_json(metadata),
                        now,
                        udp_id,
                    ],
                )
            else:
                con.execute(
                    """
                    INSERT INTO udp_definitions
                    (id, user_id, process_graph, parameters, metadata, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        udp_id,
                        user_id,
                        _serialize_json(process_graph),
                        _serialize_json(parameters),
                        _serialize_json(metadata),
                        now,
                        now,
                    ],
                )

        return udp_id

    def delete_udp(self, user_id: str, udp_id: str) -> bool:
        """Delete a UDP for a user."""
        with duckdb.connect(self.store) as con:
            result = con.execute(
                """
                DELETE FROM udp_definitions
                WHERE id = ? AND user_id = ?
                RETURNING id
                """,
                [udp_id, user_id],
            ).fetchone()

            if result is None:
                raise ValueError(f"Could not find UDP {udp_id} for user {user_id}")

        return True

    def _row_to_dict(self, row: Any) -> Dict[str, Any]:
        """Convert a duckdb row into a UDP dict."""
        return {
            "id": row[0],
            "user_id": row[1],
            "process_graph": _deserialize_json(row[2]),
            "parameters": _deserialize_json(row[3]),
            "metadata": _deserialize_json(row[4]),
            "created_at": row[5],
            "updated_at": row[6],
        }
