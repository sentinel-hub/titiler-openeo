"""Test the SQLAlchemy connection pool configuration."""


def test_store_pool_settings_reach_the_engine(monkeypatch):
    """Pool sizes are configurable and one engine serves both stores (#410)."""
    from unittest.mock import patch

    from titiler.openeo.services import sqlalchemy as module

    monkeypatch.setenv("TITILER_OPENEO_STORE_POOL_SIZE", "2")
    monkeypatch.setenv("TITILER_OPENEO_STORE_MAX_OVERFLOW", "3")
    monkeypatch.setenv("TITILER_OPENEO_STORE_POOL_TIMEOUT", "7")
    monkeypatch.setenv("TITILER_OPENEO_STORE_POOL_RECYCLE", "300")
    monkeypatch.setenv("TITILER_OPENEO_STORE_POOL_PRE_PING", "true")
    monkeypatch.setattr(module, "_engines", {})

    with (
        patch.object(module, "create_engine") as create_engine,
        patch.object(module.Base.metadata, "create_all"),
    ):
        services = module.SQLAlchemyStore(store="postgresql://u:p@localhost/db")
        udps = module.SQLAlchemyUdpStore(store="postgresql://u:p@localhost/db")
        module.SQLAlchemyUdpStore(store="postgresql://u:p@localhost/other")

    assert services._engine is udps._engine
    assert create_engine.call_count == 2
    assert create_engine.call_args_list[0].kwargs == {
        "pool_size": 2,
        "max_overflow": 3,
        "pool_timeout": 7,
        "pool_recycle": 300,
        "pool_pre_ping": True,
    }
