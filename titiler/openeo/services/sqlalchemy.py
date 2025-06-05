"""titiler.openeo.services SQLAlchemy."""

import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from attrs import define, field
from sqlalchemy import JSON, Column, DateTime, Integer, StaticPool, String, UniqueConstraint, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from titiler.openeo.auth import User

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


class UserTracking(Base):
    """SQLAlchemy User Tracking Model."""

    __tablename__ = "user_tracking"

    user_id = Column(String, primary_key=True)
    provider = Column(String, primary_key=True)
    first_login = Column(DateTime, nullable=False)
    last_login = Column(DateTime, nullable=False)
    login_count = Column(Integer, default=1, nullable=False)
    email = Column(String, nullable=True)
    name = Column(String, nullable=True)

    __table_args__ = (
        UniqueConstraint('user_id', 'provider', name='uix_user_provider'),
    )


@define(kw_only=True)
class SQLAlchemyStore(ServicesStore):
    """SQLAlchemy Service Store."""

    store: str = field()
    _engine: Any = field(default=None, init=False)
    _session_factory: Any = field(default=None, init=False)

    def __attrs_post_init__(self):
        """Post init: create engine and session factory."""
        # Check if the store is a sqlite in memory database
        kwargs = {}
        if self.store == "sqlite:///:memory:":
            # the same connection object must be shared among threads, 
            # since the database exists only within the scope of that connection.
            kwargs = {"connect_args": {"check_same_thread": False}, "poolclass": StaticPool}
        self._engine = create_engine(self.store, **kwargs)
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

            # Create a new dict to ensure SQLAlchemy detects the change
            service_data = {**result.service, **val}
            result.service = service_data

            session.commit()

        return item_id

    def track_user_login(self, user: User, provider: str) -> None:
        """Track user login activity."""
        now = datetime.utcnow()
        
        with Session(self._engine) as session:
            tracking = session.execute(
                select(UserTracking).where(
                    UserTracking.user_id == user.user_id,
                    UserTracking.provider == provider
                )
            ).scalar_one_or_none()

            if tracking:
                tracking.last_login = now
                tracking.login_count += 1
                if user.email:
                    tracking.email = user.email
                if user.name:
                    tracking.name = user.name
            else:
                tracking = UserTracking(
                    user_id=user.user_id,
                    provider=provider,
                    first_login=now,
                    last_login=now,
                    login_count=1,
                    email=user.email,
                    name=user.name
                )
                session.add(tracking)

            session.commit()

    def get_user_tracking(self, user_id: str, provider: str) -> Optional[Dict[str, Any]]:
        """Get user tracking information."""
        with Session(self._engine) as session:
            tracking = session.execute(
                select(UserTracking).where(
                    UserTracking.user_id == user_id,
                    UserTracking.provider == provider
                )
            ).scalar_one_or_none()

            if not tracking:
                return None

            return {
                "user_id": tracking.user_id,
                "provider": tracking.provider,
                "first_login": tracking.first_login,
                "last_login": tracking.last_login,
                "login_count": tracking.login_count,
                "email": tracking.email,
                "name": tracking.name
            }
