"""Startup of the SQLAlchemy stores while PostgreSQL is not ready yet."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.exc import OperationalError

from titiler.openeo.services import sqlalchemy as module

REFUSED = OperationalError("SELECT 1", {}, Exception("Connection refused"))


@pytest.fixture
def clock(monkeypatch):
    """A fake clock that `time.sleep` advances, so the tests do not wait."""
    now = SimpleNamespace(t=0.0)

    def sleep(seconds):
        now.t += seconds

    monkeypatch.setattr(
        module, "time", SimpleNamespace(monotonic=lambda: now.t, sleep=sleep)
    )
    return now


def _postgres_engine(*connect_results):
    """An engine whose `connect()` raises or succeeds in turn."""
    engine = MagicMock()
    engine.dialect.name = "postgresql"
    engine.connect.side_effect = connect_results
    conn = engine.begin.return_value.__enter__.return_value
    return engine, conn


@patch.object(module.Base.metadata, "create_all")
def test_create_schema_waits_for_the_database(create_all, clock):
    """Connecting is retried until PostgreSQL accepts, then the schema is created once."""
    engine, conn = _postgres_engine(REFUSED, REFUSED, MagicMock())

    module.create_schema(engine)

    assert engine.connect.call_count == 3
    assert clock.t == 2 * module._RETRY_INTERVAL
    engine.begin.assert_called_once()
    assert conn.execute.call_args.args[1] == {"key": module._SCHEMA_LOCK_KEY}
    create_all.assert_called_once_with(conn)


@patch.object(module.Base.metadata, "create_all")
def test_create_schema_gives_up_after_the_startup_timeout(create_all, clock):
    """A database that stays unreachable still fails startup, once the wait is over."""
    engine, _ = _postgres_engine(*[REFUSED] * 100)

    with pytest.raises(OperationalError):
        module.create_schema(engine)

    assert clock.t == module._STARTUP_TIMEOUT
    assert engine.connect.call_count == module._STARTUP_TIMEOUT + 1
    engine.begin.assert_not_called()
    create_all.assert_not_called()


@patch.object(module.Base.metadata, "create_all")
def test_create_schema_does_not_retry_once_connected(create_all, clock):
    """An error after connecting (here on the lock) fails at once, as before."""
    engine, conn = _postgres_engine(MagicMock())
    conn.execute.side_effect = OperationalError("SELECT 1", {}, Exception("disk full"))

    with pytest.raises(OperationalError):
        module.create_schema(engine)

    assert engine.connect.call_count == 1
    assert clock.t == 0
    create_all.assert_not_called()
