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

    # Another user cannot overwrite the same id
    with pytest.raises(ValueError):
        udp_store.upsert_udp(
            user_id="user2",
            udp_id=udp_id,
            process_graph={"process_id": "noop"},
        )

    # Another user cannot delete it
    with pytest.raises(ValueError):
        udp_store.delete_udp(user_id="user2", udp_id=udp_id)

    # Original user can delete
    assert udp_store.delete_udp(user_id="user1", udp_id=udp_id) is True
    assert udp_store.get_udp(user_id="user1", udp_id=udp_id) is None


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
