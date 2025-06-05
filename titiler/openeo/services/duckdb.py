"""titiler.openeo.services duckDB."""

import json
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

import duckdb
from attrs import define, field

from titiler.openeo.auth import User
from .base import ServicesStore


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
        now = datetime.utcnow()
        
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
                    [user.user_id, provider]
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
                        [now, user.email, user.name, user.user_id, provider]
                    )
                else:
                    # Insert new record if update affected no rows
                    con.execute(
                        """
                        INSERT INTO user_tracking 
                        (user_id, provider, first_login, last_login, login_count, email, name)
                        VALUES (?, ?, ?, ?, 1, ?, ?)
                        """,
                        [user.user_id, provider, now, now, user.email, user.name]
                    )
                con.execute("COMMIT")
            except Exception:
                con.execute("ROLLBACK")
                raise

    def get_user_tracking(self, user_id: str, provider: str) -> Optional[Dict[str, Any]]:
        """Get user tracking information."""
        with duckdb.connect(self.store) as con:
            result = con.execute(
                """
                SELECT user_id, provider, first_login, last_login, 
                       login_count, email, name
                FROM user_tracking
                WHERE user_id = ? AND provider = ?
                """,
                [user_id, provider]
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
                "name": result[6]
            }
