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


def _postgres_engine(*begin_results):
    engine = MagicMock()
    engine.dialect.name = "postgresql"
    engine.begin.side_effect = begin_results
    return engine


@patch.object(module.Base.metadata, "create_all")
def test_create_schema_waits_for_the_database(create_all, clock):
    """Connection errors at startup are retried until PostgreSQL accepts."""
    engine = _postgres_engine(REFUSED, REFUSED, MagicMock())

    module.create_schema(engine)

    assert engine.begin.call_count == 3
    create_all.assert_called_once()
    assert clock.t == 2


@patch.object(module.Base.metadata, "create_all")
def test_create_schema_gives_up_after_the_startup_timeout(create_all, clock):
    """A database that stays unreachable still fails startup, once the wait is over."""
    engine = _postgres_engine(*[REFUSED] * 100)

    with pytest.raises(OperationalError):
        module.create_schema(engine)

    assert clock.t == module._STARTUP_TIMEOUT
    assert engine.begin.call_count == module._STARTUP_TIMEOUT + 1
    create_all.assert_not_called()
