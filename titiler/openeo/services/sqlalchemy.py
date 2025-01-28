"""titiler.openeo.services SQLAlchemy."""

import uuid
from typing import Any, Dict, List, Optional

from attrs import define, field
from sqlalchemy import JSON, Column, String, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .base import ServicesStore


class Base(DeclarativeBase):
    """Base class for SQLAlchemy models."""

    pass


class Service(Base):
    """SQLAlchemy Service Model."""

    __tablename__ = "services"

    service_id = Column(String, primary_key=True)
    user_id = Column(String)
    service = Column(JSON)


@define(kw_only=True, init=False)
class SQLAlchemyStore(ServicesStore):
    """SQLAlchemy Service Store."""
    
    store: str = field()

    _engine = None
    _session_factory = None

    def __attrs_post_init__(self):
        """Post init: create engine and session factory."""
        # Convert psycopg connection params to SQLAlchemy URL
        self._engine = create_engine(self.store)
        self._session_factory = sessionmaker(bind=self._engine)

        # Create tables if they don't exist
        Base.metadata.create_all(self._engine)

    def get_service(self, service_id: str) -> Optional[Dict]:
        """Return a specific Service."""
        with Session(self._engine) as session:
            result = session.execute(
                select(Service).where(Service.service_id == service_id)
            ).scalar_one_or_none()

            if not result:
                return None

            return {
                "id": result.service_id,
                **result.service,
            }

    def get_services(self, **kwargs) -> List[Dict]:
        """Return All Services."""
        with Session(self._engine) as session:
            results = session.execute(select(Service)).scalars().all()

            return [
                {
                    "id": result.service_id,
                    **result.service,
                }
                for result in results
            ]

    def get_user_services(self, user_id: str, **kwargs) -> List[Dict]:
        """Return List Services for a user."""
        with Session(self._engine) as session:
            results = (
                session.execute(select(Service).where(Service.user_id == user_id))
                .scalars()
                .all()
            )

            if not results:
                raise ValueError(f"Could not find service for user: {user_id}")

            return [
                {
                    "id": result.service_id,
                    **result.service,
                }
                for result in results
            ]

    def add_service(self, user_id: str, service: Dict, **kwargs) -> str:
        """Add Service."""
        service_id = str(uuid.uuid4())
        with Session(self._engine) as session:
            new_service = Service(
                service_id=service_id,
                user_id=user_id,
                service=service,
            )
            session.add(new_service)
            session.commit()
        return service_id

    def delete_service(self, service_id: str, **kwargs) -> bool:
        """Delete Service."""
        with Session(self._engine) as session:
            result = session.execute(
                select(Service).where(Service.service_id == service_id)
            ).scalar_one_or_none()

            if not result:
                raise ValueError(f"Could not find service: {service_id}")

            session.delete(result)
            session.commit()

        return True

    def update_service(
        self, user_id: str, item_id: str, val: Dict[str, Any], **kwargs
    ) -> str:
        """Update Service."""
        with Session(self._engine) as session:
            result = session.execute(
                select(Service).where(Service.service_id == item_id)
            ).scalar_one_or_none()

            if not result:
                raise ValueError(f"Could not find service: {item_id}")

            if result.user_id != user_id:
                raise ValueError(f"Service {item_id} does not belong to user {user_id}")

            # Merge the existing service with updates
            service_data = result.service
            service_data.update(val)
            result.service = service_data

            session.commit()

        return item_id
