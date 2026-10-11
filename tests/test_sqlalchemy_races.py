"""Concurrent startup and login against PostgreSQL, as with several replicas.

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


def test_concurrent_startup_on_empty_database(empty_database):
    """Every store creates the schema at once without a crash (#274)."""

    def start():
        module.SQLAlchemyStore(store=empty_database)
        module.SQLAlchemyUdpStore(store=empty_database)
        SQLAlchemyTileStore(empty_database)

    _all_at_once(start)


def test_concurrent_first_login(empty_database):
    """Concurrent first logins of one user count each login once (#275)."""
    store = module.SQLAlchemyStore(store=empty_database)
    user = User(user_id="user", email="user@example.com")

    _all_at_once(lambda: store.track_user_login(user=user, provider="oidc"))

    tracking = store.get_user_tracking(user_id="user", provider="oidc")
    assert tracking is not None
    assert tracking["login_count"] == REPLICAS
