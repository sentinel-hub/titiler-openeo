"""Startup of the SQLAlchemy stores while PostgreSQL is not ready yet."""

from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.exc import OperationalError

from titiler.openeo.services import sqlalchemy as module

REFUSED = OperationalError("SELECT 1", {}, Exception("Connection refused"))


def _postgres_engine(*connect_results):
    """An engine whose `connect()` raises or succeeds in turn."""
    engine = MagicMock()
    engine.dialect.name = "postgresql"
    engine.connect.side_effect = connect_results
    return engine, engine.begin.return_value.__enter__.return_value


@patch.object(module.time, "sleep")
@patch.object(module.Base.metadata, "create_all")
def test_create_schema_waits_for_the_database(create_all, sleep):
    """Connecting is retried until PostgreSQL accepts, then the schema is created once."""
    engine, conn = _postgres_engine(REFUSED, REFUSED, MagicMock())

    module.create_schema(engine)

    assert engine.connect.call_count == 3
    assert sleep.call_count == 2
    assert conn.execute.call_args.args[1] == {"key": module._SCHEMA_LOCK_KEY}
    create_all.assert_called_once_with(conn)


@patch.object(module.time, "sleep")
@patch.object(module.Base.metadata, "create_all")
def test_create_schema_gives_up(create_all, sleep):
    """A database that stays unreachable still fails startup, after the attempts."""
    engine, _ = _postgres_engine(*[REFUSED] * module._STARTUP_ATTEMPTS)
    engine.begin.side_effect = REFUSED

    with pytest.raises(OperationalError):
        module.create_schema(engine)

    assert engine.connect.call_count == module._STARTUP_ATTEMPTS
    create_all.assert_not_called()


@patch.object(module.time, "sleep")
@patch.object(module.Base.metadata, "create_all")
def test_create_schema_does_not_retry_once_connected(create_all, sleep):
    """An error after connecting (here on the lock) fails at once, as before."""
    engine, conn = _postgres_engine(MagicMock())
    conn.execute.side_effect = OperationalError("SELECT 1", {}, Exception("disk full"))

    with pytest.raises(OperationalError):
        module.create_schema(engine)

    sleep.assert_not_called()
    create_all.assert_not_called()
