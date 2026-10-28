"""The schema command, `python -m titiler.openeo.services.create_schema`."""

import os
import subprocess
import sys

import pytest
from sqlalchemy import create_engine, inspect

from titiler.openeo.services import create_schema as command

TABLES = {"services", "user_tracking", "udp_definitions", "tile_assignments"}


def _run(tmp_path, check=True, **urls):
    """Run the command in a fresh interpreter, as the chart's Job does.

    In this process, other tests have already imported the tile store's model,
    which would hide a command that forgets it.
    """
    env = {k: v for k, v in os.environ.items() if not k.startswith("TITILER_OPENEO_")}
    env.update({f"TITILER_OPENEO_{name.upper()}": url for name, url in urls.items()})
    return subprocess.run(
        [sys.executable, "-m", "titiler.openeo.services.create_schema"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=check,
    )


def _tables(url):
    engine = create_engine(url)
    try:
        return set(inspect(engine).get_table_names())
    finally:
        engine.dispose()


def test_command_creates_every_table(tmp_path):
    """One run creates the tables of every store."""
    url = f"sqlite:///{tmp_path / 's.db'}"

    _run(tmp_path, store_url=url)

    assert _tables(url) == TABLES


def test_command_creates_tables_on_the_tile_store_too(tmp_path):
    """A tile store on its own database gets its tables as well."""
    store = f"sqlite:///{tmp_path / 'a.db'}"
    tiles = f"sqlite:///{tmp_path / 'b.db'}"

    _run(tmp_path, store_url=store, tile_store_url=tiles)

    assert _tables(store) == TABLES
    assert _tables(tiles) == TABLES


def test_command_skips_a_store_that_is_not_sql(tmp_path):
    """A JSON services store is skipped, and its SQL tile store gets its tables."""
    tiles = f"sqlite:///{tmp_path / 'b.db'}"

    _run(tmp_path, store_url=str(tmp_path / "store.json"), tile_store_url=tiles)

    assert _tables(tiles) == TABLES


@pytest.mark.parametrize("store", ["", "/data/store.json"], ids=["empty", "json"])
def test_command_fails_without_a_sql_database(tmp_path, store):
    """With no SQL database to prepare, the Job fails instead of passing idle."""
    result = _run(tmp_path, check=False, store_url=store)

    assert result.returncode != 0
    assert "No SQL database" in result.stderr


def test_command_without_store_url_hides_the_tile_store_password(tmp_path):
    """A missing store URL is not a validation error that echoes the tile DSN."""
    pytest.importorskip("psycopg2")
    # Nothing listens on port 1, so connecting fails at once.
    tiles = "postgresql://u:s3cret@127.0.0.1:1/db"

    result = _run(tmp_path, check=False, tile_store_url=tiles)

    assert result.returncode != 0
    assert "s3cret" not in result.stdout + result.stderr


def test_command_hides_the_password(tmp_path, monkeypatch, capsys):
    """The Job log shows which database, never its password."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TITILER_OPENEO_STORE_URL", "postgresql://u:s3cret@h/db")
    monkeypatch.delenv("TITILER_OPENEO_TILE_STORE_URL", raising=False)
    monkeypatch.setattr(command, "_get_engine", lambda url: None)
    monkeypatch.setattr(command, "create_schema", lambda engine: None)

    command.main()

    out = capsys.readouterr().out
    assert "postgresql://u:***@h/db" in out
    assert "s3cret" not in out
