"""titiler.openeo.services SQLAlchemy."""

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from attrs import define, field
from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    Integer,
    StaticPool,
    String,
    UniqueConstraint,
    create_engine,
    select,
)
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from ..models.auth import User
from .base import ServicesStore, UdpStore


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
        UniqueConstraint("user_id", "provider", name="uix_user_provider"),
    )


class UdpDefinition(Base):
    """SQLAlchemy UDP Definition Model."""

    __tablename__ = "udp_definitions"

    id = Column(String, primary_key=True)
    user_id = Column(String, nullable=False)
    process_graph = Column(JSON, nullable=False)
    parameters = Column(JSON, nullable=True)
    metadata = Column(JSON, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(
        DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
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
            kwargs = {
                "connect_args": {"check_same_thread": False},
                "poolclass": StaticPool,
            }
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
        now = datetime.now(timezone.utc)

        with Session(self._engine) as session:
            tracking = session.execute(
                select(UserTracking).where(
                    UserTracking.user_id == user.user_id,
                    UserTracking.provider == provider,
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
                    name=user.name,
                )
                session.add(tracking)

            session.commit()

    def get_user_tracking(
        self, user_id: str, provider: str
    ) -> Optional[Dict[str, Any]]:
        """Get user tracking information."""
        with Session(self._engine) as session:
            tracking = session.execute(
                select(UserTracking).where(
                    UserTracking.user_id == user_id, UserTracking.provider == provider
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
                "name": tracking.name,
            }


@define(kw_only=True)
class SQLAlchemyUdpStore(UdpStore):
    """SQLAlchemy UDP Store."""

    store: str = field()
    _engine: Any = field(default=None, init=False)
    _session_factory: Any = field(default=None, init=False)

    def __attrs_post_init__(self):
        """Post init: create engine and session factory."""
        kwargs = {}
        if self.store == "sqlite:///:memory:":
            kwargs = {
                "connect_args": {"check_same_thread": False},
                "poolclass": StaticPool,
            }
        self._engine = create_engine(self.store, **kwargs)
        self._session_factory = sessionmaker(bind=self._engine)
        Base.metadata.create_all(self._engine)

    def list_udps(
        self, user_id: str, limit: int = 100, offset: int = 0
    ) -> List[Dict[str, Any]]:
        """List UDPs for a user."""
        with Session(self._engine) as session:
            results = (
                session.execute(
                    select(UdpDefinition)
                    .where(UdpDefinition.user_id == user_id)
                    .order_by(UdpDefinition.created_at.desc())
                    .limit(limit)
                    .offset(offset)
                )
                .scalars()
                .all()
            )

            return [self._to_dict(item) for item in results]

    def get_udp(self, user_id: str, udp_id: str) -> Optional[Dict[str, Any]]:
        """Get a single UDP for a user."""
        with Session(self._engine) as session:
            result = session.execute(
                select(UdpDefinition).where(
                    UdpDefinition.id == udp_id, UdpDefinition.user_id == user_id
                )
            ).scalar_one_or_none()

            return self._to_dict(result) if result else None

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
        with Session(self._engine) as session:
            existing = session.execute(
                select(UdpDefinition).where(UdpDefinition.id == udp_id)
            ).scalar_one_or_none()

            if existing and existing.user_id != user_id:
                raise ValueError(f"UDP {udp_id} does not belong to user {user_id}")

            if existing:
                existing.process_graph = process_graph
                existing.parameters = parameters
                existing.metadata = metadata
                existing.updated_at = now
            else:
                new_udp = UdpDefinition(
                    id=udp_id,
                    user_id=user_id,
                    process_graph=process_graph,
                    parameters=parameters,
                    metadata=metadata,
                    created_at=now,
                    updated_at=now,
                )
                session.add(new_udp)

            session.commit()
            return udp_id

    def delete_udp(self, user_id: str, udp_id: str) -> bool:
        """Delete a UDP for a user."""
        with Session(self._engine) as session:
            result = session.execute(
                select(UdpDefinition).where(
                    UdpDefinition.id == udp_id, UdpDefinition.user_id == user_id
                )
            ).scalar_one_or_none()

            if not result:
                raise ValueError(f"Could not find UDP {udp_id} for user {user_id}")

            session.delete(result)
            session.commit()
            return True

    def _to_dict(self, udp: UdpDefinition) -> Dict[str, Any]:
        """Serialize UDP model to dict."""
        return {
            "id": udp.id,
            "user_id": udp.user_id,
            "process_graph": udp.process_graph,
            "parameters": udp.parameters,
            "metadata": udp.metadata,
            "created_at": udp.created_at,
            "updated_at": udp.updated_at,
        }
