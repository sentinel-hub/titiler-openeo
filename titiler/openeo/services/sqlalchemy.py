"""titiler.openeo.services SQLAlchemy."""

import uuid
from datetime import datetime, timezone
from threading import Lock
from typing import Any, Dict, List, Optional

from attrs import define, field
from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Integer,
    StaticPool,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    select,
    text,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from ..models.auth import User
from ..settings import StoreSettings
from .base import ServicesStore, UdpStore


class Base(DeclarativeBase):
    """Base class for SQLAlchemy models."""

    pass


class Service(Base):
    """SQLAlchemy Service Model."""

    __tablename__ = "services"

    service_id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(String)
    service: Mapped[Dict[str, Any]] = mapped_column(JSON)


class UserTracking(Base):
    """SQLAlchemy User Tracking Model."""

    __tablename__ = "user_tracking"

    user_id: Mapped[str] = mapped_column(String, primary_key=True)
    provider: Mapped[str] = mapped_column(String, primary_key=True)
    first_login: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    last_login: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    login_count: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    email: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    name: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    __table_args__ = (
        UniqueConstraint("user_id", "provider", name="uix_user_provider"),
    )


class UserSession(Base):
    """SQLAlchemy User Session Model: one row per session start."""

    __tablename__ = "user_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String, nullable=False)
    provider: Mapped[str] = mapped_column(String, nullable=False)
    session_id: Mapped[str] = mapped_column(String, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)

    __table_args__ = (
        UniqueConstraint("provider", "session_id", name="uix_provider_session"),
    )


class UdpDefinition(Base):
    """SQLAlchemy UDP Definition Model."""

    __tablename__ = "udp_definitions"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(String, nullable=False)
    process_graph: Mapped[Dict[str, Any]] = mapped_column(JSON, nullable=False)
    summary: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    parameters: Mapped[Optional[List[Dict[str, Any]]]] = mapped_column(
        JSON, nullable=True
    )
    returns: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON, nullable=True)
    categories: Mapped[List[str]] = mapped_column(JSON, default=list, nullable=False)
    deprecated: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    experimental: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    exceptions: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON, nullable=True)
    examples: Mapped[Optional[List[Dict[str, Any]]]] = mapped_column(
        JSON, nullable=True
    )
    links: Mapped[Optional[List[Dict[str, Any]]]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )


_engines: Dict[str, Any] = {}
_engines_lock = Lock()


def _get_engine(store: str) -> Any:
    """Return the engine for a store URL.

    Server databases get one engine per URL and process, shared by the services
    and UDP stores, so the two do not each hold a connection pool against the
    same database. SQLite gets a fresh engine each time: an in-memory database
    exists only within its connection, so sharing would leak data between
    stores.
    """
    if store == "sqlite:///:memory:":
        # the same connection object must be shared among threads,
        # since the database exists only within the scope of that connection.
        return create_engine(
            store,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
    if store.startswith("sqlite"):
        return create_engine(store)

    with _engines_lock:
        engine = _engines.get(store)
        if engine is None:
            engine = _engines[store] = create_engine(
                store, **StoreSettings().engine_kwargs()
            )
        return engine


@define(kw_only=True)
class SQLAlchemyStore(ServicesStore):
    """SQLAlchemy Service Store."""

    store: str = field()
    _engine: Any = field(default=None, init=False)
    _session_factory: Any = field(default=None, init=False)

    def __attrs_post_init__(self):
        """Post init: create engine and session factory."""
        self._engine = _get_engine(self.store)
        self._session_factory = sessionmaker(bind=self._engine)

        # Create tables if they don't exist

        Base.metadata.create_all(self._engine)

    def ping(self) -> None:
        """Verify the SQLAlchemy store is reachable. Raises on failure."""
        with self._engine.connect() as conn:
            conn.execute(text("SELECT 1"))

    def get_service(self, service_id: str) -> Optional[Dict]:
        """Return a specific Service."""
        with Session(self._engine) as session:
            result: Optional[Service] = session.execute(
                select(Service).where(Service.service_id == service_id)
            ).scalar_one_or_none()

            if result is None:
                return None

            return {
                "id": result.service_id,
                **result.service,
                "user_id": result.user_id,
            }

    def get_services(self, **kwargs) -> List[Dict]:
        """Return All Services."""
        with Session(self._engine) as session:
            results = session.execute(select(Service)).scalars().all()

            return [
                {
                    "id": result.service_id,
                    **result.service,
                    "user_id": result.user_id,
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
                    "user_id": result.user_id,
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
            result: Optional[Service] = session.execute(
                select(Service).where(Service.service_id == service_id)
            ).scalar_one_or_none()

            if result is None:
                raise ValueError(f"Could not find service: {service_id}")

            session.delete(result)
            session.commit()

        return True

    def update_service(
        self, user_id: str, item_id: str, val: Dict[str, Any], **kwargs
    ) -> str:
        """Update Service."""
        with Session(self._engine) as session:
            result: Optional[Service] = session.execute(
                select(Service).where(Service.service_id == item_id)
            ).scalar_one_or_none()

            if result is None:
                raise ValueError(f"Could not find service: {item_id}")

            if result.user_id != user_id:
                raise ValueError(f"Service {item_id} does not belong to user {user_id}")

            # Create a new dict to ensure SQLAlchemy detects the change
            service_data = {**result.service, **val}
            result.service = service_data

            session.commit()

        return item_id

    def record_session(self, user: User, provider: str, session_id: str) -> bool:
        """Record the start of a user session."""
        now = datetime.now(timezone.utc)

        with Session(self._engine) as session:
            # Idempotent across replicas: the unique constraint decides.
            session.add(
                UserSession(
                    user_id=user.user_id,
                    provider=provider,
                    session_id=session_id,
                    started_at=now,
                )
            )
            try:
                session.flush()
            except IntegrityError:
                session.rollback()
                return False

            tracking: Optional[UserTracking] = session.execute(
                select(UserTracking).where(
                    UserTracking.user_id == user.user_id,
                    UserTracking.provider == provider,
                )
            ).scalar_one_or_none()

            if tracking is not None:
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
        return True

    def get_user_sessions(
        self, user_id: str, provider: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """List the recorded sessions of a user, oldest first."""
        query = select(UserSession).where(UserSession.user_id == user_id)
        if provider is not None:
            query = query.where(UserSession.provider == provider)

        with Session(self._engine) as session:
            rows = session.execute(query.order_by(UserSession.id)).scalars().all()
            return [
                {
                    "user_id": row.user_id,
                    "provider": row.provider,
                    "session_id": row.session_id,
                    "started_at": row.started_at,
                }
                for row in rows
            ]

    def get_user_tracking(
        self, user_id: str, provider: str
    ) -> Optional[Dict[str, Any]]:
        """Get user tracking information."""
        with Session(self._engine) as session:
            tracking: Optional[UserTracking] = session.execute(
                select(UserTracking).where(
                    UserTracking.user_id == user_id, UserTracking.provider == provider
                )
            ).scalar_one_or_none()

            if tracking is None:
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
        self._engine = _get_engine(self.store)
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
            result: Optional[UdpDefinition] = session.execute(
                select(UdpDefinition).where(
                    UdpDefinition.id == udp_id, UdpDefinition.user_id == user_id
                )
            ).scalar_one_or_none()

            return self._to_dict(result) if result is not None else None

    def upsert_udp(
        self,
        user_id: str,
        udp_id: str,
        process_graph: Dict[str, Any],
        summary: Optional[str] = None,
        description: Optional[str] = None,
        parameters: Optional[List[Dict[str, Any]]] = None,
        returns: Optional[Dict[str, Any]] = None,
        categories: Optional[List[str]] = None,
        deprecated: bool = False,
        experimental: bool = False,
        exceptions: Optional[Dict[str, Any]] = None,
        examples: Optional[List[Dict[str, Any]]] = None,
        links: Optional[List[Dict[str, Any]]] = None,
    ) -> str:
        """Create or replace a UDP for a user."""
        now = datetime.utcnow()
        with Session(self._engine) as session:
            existing: Optional[UdpDefinition] = session.execute(
                select(UdpDefinition).where(UdpDefinition.id == udp_id)
            ).scalar_one_or_none()

            if existing is not None and existing.user_id != user_id:
                raise ValueError(f"UDP {udp_id} does not belong to user {user_id}")

            if existing is not None:
                existing.process_graph = process_graph
                existing.parameters = parameters
                existing.summary = summary
                existing.description = description
                existing.returns = returns
                existing.categories = categories or []
                existing.deprecated = deprecated
                existing.experimental = experimental
                existing.exceptions = exceptions
                existing.examples = examples
                existing.links = links
                existing.updated_at = now
            else:
                new_udp = UdpDefinition(
                    id=udp_id,
                    user_id=user_id,
                    process_graph=process_graph,
                    parameters=parameters,
                    summary=summary,
                    description=description,
                    returns=returns,
                    categories=categories or [],
                    deprecated=deprecated,
                    experimental=experimental,
                    exceptions=exceptions,
                    examples=examples,
                    links=links,
                    created_at=now,
                    updated_at=now,
                )
                session.add(new_udp)

            session.commit()
            return udp_id

    def delete_udp(self, user_id: str, udp_id: str) -> bool:
        """Delete a UDP for a user."""
        with Session(self._engine) as session:
            result: Optional[UdpDefinition] = session.execute(
                select(UdpDefinition).where(
                    UdpDefinition.id == udp_id, UdpDefinition.user_id == user_id
                )
            ).scalar_one_or_none()

            if result is None:
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
            "summary": udp.summary,
            "description": udp.description,
            "returns": udp.returns,
            "categories": udp.categories or [],
            "deprecated": udp.deprecated,
            "experimental": udp.experimental,
            "exceptions": udp.exceptions,
            "examples": udp.examples,
            "links": udp.links,
            "created_at": udp.created_at,
            "updated_at": udp.updated_at,
        }
