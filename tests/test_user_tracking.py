"""Test user tracking functionality."""

from titiler.openeo.auth import User


def test_user_tracking_first_login(store_path):
    """Test first-time user login tracking."""
    from titiler.openeo.services import get_store

    store = get_store(f"{store_path}")
    user = User(user_id="test_user", email="test@example.com", name="Test User")

    # Track first login
    store.record_session(user=user, provider="basic", session_id="s1")

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
    store.record_session(user=user, provider="basic", session_id="s2")
    first_tracking = store.get_user_tracking(user_id="test_user", provider="basic")
    assert first_tracking is not None
    first_login = first_tracking["first_login"]
    last_login = first_tracking["last_login"]

    # Second login
    store.record_session(user=user, provider="basic", session_id="s3")

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
    store.record_session(user=user, provider="basic", session_id="s4")
    store.record_session(user=user, provider="oidc", session_id="s5")

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
    store.record_session(user=user1, provider="basic", session_id="s6")

    # Second login with updated info
    user2 = User(user_id="test_user", email="new@example.com", name="New Name")
    store.record_session(user=user2, provider="basic", session_id="s7")

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


def test_record_session_is_idempotent(store_path):
    """The same (provider, session_id) is one session, however often it arrives."""
    from titiler.openeo.services import get_store

    store = get_store(f"{store_path}")
    user = User(user_id="test_user")

    assert store.record_session(user, "oidc", "sid-1") is True
    assert store.record_session(user, "oidc", "sid-1") is False
    assert store.record_session(user, "oidc", "sid-2") is True

    sessions = store.get_user_sessions("test_user", "oidc")
    assert [s["session_id"] for s in sessions] == ["sid-1", "sid-2"]
    assert store.get_user_tracking("test_user", "oidc")["login_count"] == 2


def test_sessions_are_listed_per_user_and_provider(store_path):
    """Sessions keep their user, provider and start time."""
    from titiler.openeo.services import get_store

    store = get_store(f"{store_path}")
    store.record_session(User(user_id="a"), "basic", "s1")
    store.record_session(User(user_id="a"), "oidc", "s2")
    store.record_session(User(user_id="b"), "oidc", "s3")

    assert len(store.get_user_sessions("a")) == 2
    only_oidc = store.get_user_sessions("a", "oidc")
    assert [s["session_id"] for s in only_oidc] == ["s2"]
    assert only_oidc[0]["started_at"] is not None
    assert store.get_user_sessions("nobody") == []


def _basic_auth(store):
    from titiler.openeo.auth import BasicAuth
    from titiler.openeo.settings import AuthSettings

    return BasicAuth(
        store=store,
        settings=AuthSettings(method="basic", users={"u": {"password": "p"}}),
    )


def test_basic_login_records_a_session_but_validate_does_not():
    """A session starts at login; requests within it never write."""
    import base64
    from unittest.mock import Mock

    store = Mock()
    auth = _basic_auth(store)
    creds = base64.b64encode(b"u:p").decode()

    token = auth.login(f"Basic {creds}")
    store.record_session.assert_called_once()

    for _ in range(5):
        auth.validate(f"Bearer basic//{token.access_token}")
    store.record_session.assert_called_once()


def test_a_store_failure_does_not_fail_authentication():
    """A database error while recording must not reject a valid user."""
    import base64
    from unittest.mock import Mock

    store = Mock()
    store.record_session.side_effect = RuntimeError("too many connections")
    auth = _basic_auth(store)
    creds = base64.b64encode(b"u:p").decode()

    assert auth.login(f"Basic {creds}").access_token == creds
