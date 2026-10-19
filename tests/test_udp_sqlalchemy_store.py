"""Tests for the SQLAlchemy UDP store."""

import pytest

from titiler.openeo.services.sqlalchemy import SQLAlchemyUdpStore


@pytest.fixture
def udp_store():
    """Create an in-memory SQLAlchemy UDP store."""
    return SQLAlchemyUdpStore(store="sqlite:///:memory:")


def test_upsert_and_get_udp(udp_store):
    """Ensure UDPs can be created and retrieved."""
    udp_id = "udp-1"
    process_graph = {"process_id": "noop"}
    parameters = [{"name": "param", "description": "test"}]
    returns = {"schema": {"type": "object"}}
    summary = "Test process"
    description = "Longer description"

    udp_store.upsert_udp(
        user_id="user1",
        udp_id=udp_id,
        process_graph=process_graph,
        parameters=parameters,
        returns=returns,
        summary=summary,
        description=description,
        categories=["cat1", "cat2"],
        deprecated=True,
        experimental=True,
        exceptions={"SomeError": {"message": "bad"}},
        examples=[{"title": "ex"}],
        links=[{"href": "https://example.com"}],
    )

    stored = udp_store.get_udp(user_id="user1", udp_id=udp_id)
    assert stored is not None
    assert stored["id"] == udp_id
    assert stored["process_graph"] == process_graph
    assert stored["parameters"] == parameters
    assert stored["returns"] == returns
    assert stored["summary"] == summary
    assert stored["description"] == description
    assert stored["categories"] == ["cat1", "cat2"]
    assert stored["deprecated"] is True
    assert stored["experimental"] is True
    assert stored["exceptions"] == {"SomeError": {"message": "bad"}}
    assert stored["examples"] == [{"title": "ex"}]
    assert stored["links"] == [{"href": "https://example.com"}]
    assert stored["user_id"] == "user1"


def test_upsert_replaces_existing(udp_store):
    """Upsert replaces the existing UDP for the same user."""
    udp_id = "udp-1"
    udp_store.upsert_udp(
        user_id="user1",
        udp_id=udp_id,
        process_graph={"process_id": "first"},
        parameters=[{"a": 1}],
    )

    udp_store.upsert_udp(
        user_id="user1",
        udp_id=udp_id,
        process_graph={"process_id": "second"},
        parameters=[{"a": 2}],
        summary="updated",
    )

    stored = udp_store.get_udp(user_id="user1", udp_id=udp_id)
    assert stored["process_graph"] == {"process_id": "second"}
    assert stored["parameters"] == [{"a": 2}]
    assert stored["summary"] == "updated"


def test_user_isolation_on_upsert_and_delete(udp_store):
    """Enforce that UDP IDs are scoped per user."""
    udp_id = "udp-1"
    udp_store.upsert_udp(
        user_id="user1",
        udp_id=udp_id,
        process_graph={"process_id": "noop"},
    )

    # Another user cannot delete it
    with pytest.raises(ValueError):
        udp_store.delete_udp(user_id="user2", udp_id=udp_id)

    # Original user can delete
    assert udp_store.delete_udp(user_id="user1", udp_id=udp_id) is True
    assert udp_store.get_udp(user_id="user1", udp_id=udp_id) is None


def test_same_udp_id_for_different_users(udp_store):
    """Two users can each hold a UDP with the same id (#469)."""
    udp_id = "ndvi"
    udp_store.upsert_udp(
        user_id="user1", udp_id=udp_id, process_graph={"process_id": "one"}
    )
    udp_store.upsert_udp(
        user_id="user2", udp_id=udp_id, process_graph={"process_id": "two"}
    )

    udp1 = udp_store.get_udp(user_id="user1", udp_id=udp_id)
    udp2 = udp_store.get_udp(user_id="user2", udp_id=udp_id)
    assert udp1["process_graph"] == {"process_id": "one"}
    assert udp2["process_graph"] == {"process_id": "two"}
    assert [u["id"] for u in udp_store.list_udps(user_id="user2")] == [udp_id]

    # Replacing one user's UDP leaves the other user's UDP unchanged
    udp_store.upsert_udp(
        user_id="user2", udp_id=udp_id, process_graph={"process_id": "three"}
    )
    udp1 = udp_store.get_udp(user_id="user1", udp_id=udp_id)
    assert udp1["process_graph"] == {"process_id": "one"}

    # Deleting one user's UDP leaves the other user's UDP in place
    assert udp_store.delete_udp(user_id="user1", udp_id=udp_id) is True
    assert udp_store.get_udp(user_id="user1", udp_id=udp_id) is None
    udp2 = udp_store.get_udp(user_id="user2", udp_id=udp_id)
    assert udp2["process_graph"] == {"process_id": "three"}


def test_list_with_pagination(udp_store):
    """List returns user-scoped UDPs with pagination."""
    for idx in range(5):
        udp_store.upsert_udp(
            user_id="user1",
            udp_id=f"udp-{idx}",
            process_graph={"i": idx},
        )

    # Another user entry should not be returned
    udp_store.upsert_udp(
        user_id="user2",
        udp_id="udp-other",
        process_graph={"i": 99},
    )

    first_page = udp_store.list_udps(user_id="user1", limit=2, offset=0)
    second_page = udp_store.list_udps(user_id="user1", limit=2, offset=2)

    assert len(first_page) == 2
    assert len(second_page) == 2
    ids_seen = {item["id"] for item in first_page + second_page}
    assert ids_seen.issubset({f"udp-{i}" for i in range(5)})

    # Verify other user's UDP is not listed
    all_user1 = udp_store.list_udps(user_id="user1", limit=10, offset=0)
    assert all(item["user_id"] == "user1" for item in all_user1)
    assert not any(item["id"] == "udp-other" for item in all_user1)


def test_table_keyed_on_id_is_migrated(tmp_path):
    """A table keyed on `id` alone is rekeyed on `(user_id, id)` (#469)."""
    from sqlalchemy import create_engine, inspect, text

    url = f"sqlite:///{tmp_path / 'udp.db'}"
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE udp_definitions (
                    id VARCHAR NOT NULL PRIMARY KEY,
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
                    created_at DATETIME NOT NULL,
                    updated_at DATETIME NOT NULL
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
                VALUES ('ndvi', 'user1', '{"process_id": "one"}', '[]', 0, 0,
                        '2024-01-01 00:00:00', '2024-01-01 00:00:00')
                """
            )
        )

    udp_store = SQLAlchemyUdpStore(store=url)
    pk = inspect(create_engine(url)).get_pk_constraint("udp_definitions")
    assert sorted(pk["constrained_columns"]) == ["id", "user_id"]

    udp_store.upsert_udp(
        user_id="user2", udp_id="ndvi", process_graph={"process_id": "two"}
    )
    udp1 = udp_store.get_udp(user_id="user1", udp_id="ndvi")
    assert udp1["process_graph"] == {"process_id": "one"}
    udp2 = udp_store.get_udp(user_id="user2", udp_id="ndvi")
    assert udp2["process_graph"] == {"process_id": "two"}

    # Opening the migrated store again leaves it as it is
    reopened = SQLAlchemyUdpStore(store=url)
    assert len(reopened.list_udps(user_id="user1")) == 1
