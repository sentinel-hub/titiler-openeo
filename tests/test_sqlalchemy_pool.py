"""Test the SQLAlchemy connection pool configuration."""

from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError
from sqlalchemy import StaticPool
from sqlalchemy.engine import URL

from titiler.openeo.services import sqlalchemy as module
from titiler.openeo.services.sqlalchemy_tile import SQLAlchemyTileStore
from titiler.openeo.settings import StoreSettings


@pytest.fixture
def create_engine(monkeypatch):
    """A fake `create_engine` that returns a distinct engine on every call."""
    monkeypatch.setattr(module, "_engines", {})
    with (
        patch.object(
            module, "create_engine", side_effect=lambda *a, **k: MagicMock()
        ) as create_engine,
        patch.object(module.Base.metadata, "create_all"),
    ):
        yield create_engine


def test_store_pool_settings_reach_the_engine(monkeypatch, create_engine):
    """Pool sizes are configurable and one engine serves every store (#410)."""
    monkeypatch.setenv("TITILER_OPENEO_STORE_POOL_SIZE", "2")
    monkeypatch.setenv("TITILER_OPENEO_STORE_MAX_OVERFLOW", "3")
    monkeypatch.setenv("TITILER_OPENEO_STORE_POOL_TIMEOUT", "7")
    monkeypatch.setenv("TITILER_OPENEO_STORE_POOL_RECYCLE", "300")
    monkeypatch.setenv("TITILER_OPENEO_STORE_POOL_PRE_PING", "true")

    services = module.SQLAlchemyStore(store="postgresql://u:p@localhost/db")
    udps = module.SQLAlchemyUdpStore(store="postgresql://u:p@localhost/db")
    tiles = SQLAlchemyTileStore("postgresql://u:p@localhost/db")
    other = module.SQLAlchemyUdpStore(store="postgresql://u:p@localhost/other")

    assert services._engine is udps._engine is tiles._engine
    assert other._engine is not services._engine
    assert create_engine.call_count == 2
    assert create_engine.call_args_list[0].kwargs == {
        "pool_size": 2,
        "max_overflow": 3,
        "pool_timeout": 7,
        "pool_recycle": 300,
        "pool_pre_ping": True,
    }


def test_url_object_is_accepted_and_keyed_with_its_password(create_engine):
    """A `URL` object works, and URLs that differ by password do not share."""
    url = URL.create(
        "postgresql", username="u", password="p@ss/#", host="h", database="db"
    )
    first = module.SQLAlchemyStore(store=url)
    same = module.SQLAlchemyUdpStore(store=url.render_as_string(hide_password=False))
    other = module.SQLAlchemyUdpStore(store=url.set(password="different"))

    assert first._engine is same._engine
    assert other._engine is not first._engine


@pytest.mark.parametrize(
    "store",
    ["sqlite://", "sqlite:///:memory:", "sqlite+pysqlite:///:memory:"],
)
def test_sqlite_memory_variants_share_one_connection(create_engine, store):
    """Every in-memory spelling gets a StaticPool, so all threads see one db."""
    module.SQLAlchemyStore(store=store)

    assert create_engine.call_args.kwargs["poolclass"] is StaticPool


def test_dispose_engines_rebuilds_with_current_settings(monkeypatch, create_engine):
    """After `dispose_engines`, a new store reads the pool settings again."""
    monkeypatch.setenv("TITILER_OPENEO_STORE_POOL_SIZE", "3")
    first = module.SQLAlchemyStore(store="postgresql://u:p@localhost/db")

    monkeypatch.setenv("TITILER_OPENEO_STORE_POOL_SIZE", "2")
    module.dispose_engines()
    second = module.SQLAlchemyStore(store="postgresql://u:p@localhost/db")

    first._engine.dispose.assert_called_once()
    assert second._engine is not first._engine
    assert create_engine.call_args.kwargs["pool_size"] == 2


@pytest.mark.parametrize("value", ["0", "-2"])
def test_pool_recycle_rejects_zero_and_below_minus_one(monkeypatch, value):
    """0 means "replace on every checkout" to SQLAlchemy, not "disabled"."""
    monkeypatch.setenv("TITILER_OPENEO_STORE_POOL_RECYCLE", value)
    with pytest.raises(ValidationError):
        StoreSettings()


def test_empty_pool_variable_keeps_the_default(monkeypatch):
    """An empty variable (e.g. a Helm value set to null) is not a crash."""
    monkeypatch.setenv("TITILER_OPENEO_STORE_POOL_RECYCLE", "")
    assert StoreSettings().pool_recycle == -1
