"""The SQLAlchemy stores against a real PostgreSQL.

Set `TITILER_OPENEO_TEST_POSTGRES_URL` to a server URL whose user can create
databases (e.g. `postgresql://postgres:pg@localhost:5432/postgres`) to run them.
"""

import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from titiler.openeo.auth import User
from titiler.openeo.services import sqlalchemy as module
from titiler.openeo.services.sqlalchemy_tile import SQLAlchemyTileStore

ADMIN_URL = os.environ.get("TITILER_OPENEO_TEST_POSTGRES_URL")
REPLICAS = 8
TABLES = {"services", "user_tracking", "udp_definitions", "tile_assignments"}

pytestmark = pytest.mark.skipif(
    not ADMIN_URL, reason="TITILER_OPENEO_TEST_POSTGRES_URL is not set"
)


@pytest.fixture
def empty_database():
    """URL of a new, empty database, dropped after the test."""
    from sqlalchemy import create_engine, make_url, text

    name = f"titiler_race_{uuid.uuid4().hex[:8]}"
    admin = create_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    yield make_url(ADMIN_URL).set(database=name).render_as_string(hide_password=False)
    module.dispose_engines()
    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
    admin.dispose()


def _all_at_once(fn):
    """Run `fn` in REPLICAS threads that start together; re-raise any error."""
    barrier = Barrier(REPLICAS)

    def run():
        barrier.wait()
        return fn()

    with ThreadPoolExecutor(REPLICAS) as pool:
        return [f.result() for f in [pool.submit(run) for _ in range(REPLICAS)]]


def _tables(url):
    from sqlalchemy import inspect

    return set(inspect(module._get_engine(url)).get_table_names())


def test_create_schema_on_empty_database(empty_database):
    """The schema is created, and a second run, as on an upgrade, is a no-op."""
    engine = module._get_engine(empty_database)

    module.create_schema(engine)
    module.create_schema(engine)

    assert _tables(empty_database) == TABLES


def test_stores_leave_an_empty_database_untouched(empty_database):
    """The stores run no DDL on PostgreSQL, so replicas cannot race on it (#274)."""
    module.SQLAlchemyStore(store=empty_database)
    module.SQLAlchemyUdpStore(store=empty_database)
    SQLAlchemyTileStore(empty_database)

    assert _tables(empty_database) == set()


def test_pre_ping_reconnects_after_server_restart(
    empty_database, tmp_path, monkeypatch
):
    """A pooled connection the server dropped fails no request (pre-ping)."""
    from sqlalchemy import create_engine, make_url, text

    # Default settings: no `.env` of the repository, no pre-ping variable.
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("TITILER_OPENEO_STORE_POOL_PRE_PING", raising=False)
    store = module.SQLAlchemyStore(store=empty_database)
    module.create_schema(store._engine)
    assert store.get_services() == []

    # As a restart does, end the pooled connection's backend. With a timeout,
    # `pg_terminate_backend` waits until the backend has exited.
    admin = create_engine(ADMIN_URL)
    with admin.connect() as conn:
        terminated = (
            conn.execute(
                text(
                    "SELECT pg_terminate_backend(pid, 5000) FROM pg_stat_activity "
                    "WHERE datname = :name"
                ),
                {"name": make_url(empty_database).database},
            )
            .scalars()
            .all()
        )
    admin.dispose()
    assert terminated and all(terminated)

    assert store.get_services() == []


def test_concurrent_first_login(empty_database):
    """Concurrent first logins of one user count each login once (#275)."""
    store = module.SQLAlchemyStore(store=empty_database)
    module.create_schema(store._engine)
    user = User(user_id="user", email="user@example.com")

    _all_at_once(lambda: store.track_user_login(user=user, provider="oidc"))

    tracking = store.get_user_tracking(user_id="user", provider="oidc")
    assert tracking is not None
    assert tracking["login_count"] == REPLICAS


def test_create_schema_migrates_an_older_udp_key(empty_database):
    """The schema command rekeys an older UDP table, and a rerun is a no-op (#469)."""
    from sqlalchemy import create_engine, inspect, text

    engine = create_engine(empty_database)
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE udp_definitions (
                    id VARCHAR PRIMARY KEY,
                    user_id VARCHAR NOT NULL,
                    process_graph JSON NOT NULL,
                    summary VARCHAR,
                    description TEXT,
                    parameters JSON,
                    returns JSON,
                    categories JSON NOT NULL,
                    deprecated BOOLEAN NOT NULL,
                    experimental BOOLEAN NOT NULL,
                    exceptions JSON,
                    examples JSON,
                    links JSON,
                    created_at TIMESTAMP NOT NULL,
                    updated_at TIMESTAMP NOT NULL
                )
                """
            )
        )
        conn.execute(
            text(
                """
                INSERT INTO udp_definitions
                (id, user_id, process_graph, categories, deprecated, experimental,
                 created_at, updated_at)
                VALUES ('ndvi', 'user1', '{"process_id": "one"}', '[]', false,
                        false, now(), now())
                """
            )
        )

    module.create_schema(module._get_engine(empty_database))
    module.create_schema(module._get_engine(empty_database))

    pk = inspect(engine).get_pk_constraint("udp_definitions")
    engine.dispose()
    assert sorted(pk["constrained_columns"]) == ["id", "user_id"]

    store = module.SQLAlchemyUdpStore(store=empty_database)
    store.upsert_udp(user_id="user2", udp_id="ndvi", process_graph={"p": 2})
    assert store.get_udp(user_id="user1", udp_id="ndvi")["process_graph"] == {
        "process_id": "one"
    }
    assert store.get_udp(user_id="user2", udp_id="ndvi")["process_graph"] == {"p": 2}
