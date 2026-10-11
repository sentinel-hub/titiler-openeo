"""Test user tracking functionality."""

from titiler.openeo.auth import User


def test_user_tracking_first_login(store_path):
    """Test first-time user login tracking."""
    from titiler.openeo.services import get_store

    store = get_store(f"{store_path}")
    user = User(user_id="test_user", email="test@example.com", name="Test User")

    # Track first login
    store.track_user_login(user=user, provider="basic")

    # Get tracking info
    tracking = store.get_user_tracking(user_id="test_user", provider="basic")
    assert tracking is not None
    assert tracking["user_id"] == "test_user"
    assert tracking["provider"] == "basic"
    assert tracking["email"] == "test@example.com"
    assert tracking["name"] == "Test User"
    assert tracking["login_count"] == 1
    assert tracking["first_login"] == tracking["last_login"]


def test_user_tracking_multiple_logins(store_path):
    """Test tracking multiple logins for the same user."""
    from titiler.openeo.services import get_store

    store = get_store(f"{store_path}")
    user = User(user_id="test_user", email="test@example.com", name="Test User")

    # First login
    store.track_user_login(user=user, provider="basic")
    first_tracking = store.get_user_tracking(user_id="test_user", provider="basic")
    assert first_tracking is not None
    first_login = first_tracking["first_login"]
    last_login = first_tracking["last_login"]

    # Second login
    store.track_user_login(user=user, provider="basic")

    # Check updated tracking info
    second_tracking = store.get_user_tracking(user_id="test_user", provider="basic")
    assert second_tracking is not None
    assert second_tracking["login_count"] == 2
    assert second_tracking["first_login"] == first_login
    assert second_tracking["last_login"] > last_login


def test_user_tracking_multiple_providers(store_path):
    """Test tracking user logins with different providers."""
    from titiler.openeo.services import get_store

    store = get_store(f"{store_path}")
    user = User(user_id="test_user", email="test@example.com", name="Test User")

    # Track logins with different providers
    store.track_user_login(user=user, provider="basic")
    store.track_user_login(user=user, provider="oidc")

    # Check basic auth tracking
    basic_tracking = store.get_user_tracking(user_id="test_user", provider="basic")
    assert basic_tracking is not None
    assert basic_tracking["login_count"] == 1

    # Check OIDC tracking
    oidc_tracking = store.get_user_tracking(user_id="test_user", provider="oidc")
    assert oidc_tracking is not None
    assert oidc_tracking["login_count"] == 1


def test_user_tracking_update_info(store_path):
    """Test updating user info on subsequent logins."""
    from titiler.openeo.services import get_store

    store = get_store(f"{store_path}")

    # First login with initial info
    user1 = User(user_id="test_user", email="old@example.com", name="Old Name")
    store.track_user_login(user=user1, provider="basic")

    # Second login with updated info
    user2 = User(user_id="test_user", email="new@example.com", name="New Name")
    store.track_user_login(user=user2, provider="basic")

    # Check updated info
    tracking = store.get_user_tracking(user_id="test_user", provider="basic")
    assert tracking is not None
    assert tracking["email"] == "new@example.com"
    assert tracking["name"] == "New Name"
    assert tracking["login_count"] == 2


def test_get_user_tracking_nonexistent(store_path):
    """Test getting tracking info for nonexistent user."""
    from titiler.openeo.services import get_store

    store = get_store(f"{store_path}")
    tracking = store.get_user_tracking(user_id="nonexistent", provider="basic")
    assert tracking is None


def test_sqlalchemy_user_tracking_keeps_info_when_missing():
    """A login without email or name keeps the stored values (SQLAlchemy store)."""
    from titiler.openeo.services import get_store

    store = get_store("sqlite:///:memory:")
    store.track_user_login(
        user=User(user_id="test_user", email="a@example.com", name="A"),
        provider="basic",
    )
    store.track_user_login(user=User(user_id="test_user"), provider="basic")

    tracking = store.get_user_tracking(user_id="test_user", provider="basic")
    assert tracking is not None
    assert tracking["email"] == "a@example.com"
    assert tracking["name"] == "A"
    assert tracking["login_count"] == 2


def test_sqlalchemy_user_tracking_ignores_older_login(monkeypatch):
    """A login that commits after a newer one does not roll the record back."""
    from datetime import datetime, timezone
    from unittest.mock import MagicMock

    from titiler.openeo.services import get_store
    from titiler.openeo.services import sqlalchemy as module

    older = datetime(2026, 1, 1, tzinfo=timezone.utc)
    newer = datetime(2026, 1, 2, tzinfo=timezone.utc)
    clock = MagicMock()
    monkeypatch.setattr(module, "datetime", clock)
    store = get_store("sqlite:///:memory:")

    clock.now.return_value = newer
    store.track_user_login(
        user=User(user_id="u", email="new@example.com", name="New"), provider="oidc"
    )
    clock.now.return_value = older
    store.track_user_login(
        user=User(user_id="u", email="old@example.com", name="Old"), provider="oidc"
    )

    tracking = store.get_user_tracking(user_id="u", provider="oidc")
    assert tracking is not None
    assert tracking["first_login"] == older.replace(tzinfo=None)
    assert tracking["last_login"] == newer.replace(tzinfo=None)
    assert tracking["email"] == "new@example.com"
    assert tracking["name"] == "New"
    assert tracking["login_count"] == 2
