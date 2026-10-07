"""Tests for authentication module."""

import base64
import json
import time
from unittest.mock import MagicMock, Mock, patch

import pytest
from fastapi.exceptions import HTTPException
from pydantic import ValidationError

from titiler.openeo.auth import AuthToken, OIDCAuth, OIDCConfig
from titiler.openeo.settings import AuthSettings

ISSUER = "https://auth.example.com"
JWKS_URI = "https://auth.example.com/jwks"
WK_URL = "https://auth.example.com/.well-known/openid-configuration"
DISCOVERY = {"issuer": ISSUER, "jwks_uri": JWKS_URI}

# Microsoft Entra multi-tenant (`common`) endpoint.
ENTRA_COMMON_WK_URL = (
    "https://login.microsoftonline.com/common/v2.0/.well-known/openid-configuration"
)
ENTRA_TEMPLATE = "https://login.microsoftonline.com/{tenantid}/v2.0"
TENANT_A = "11111111-1111-1111-1111-111111111111"
TENANT_B = "22222222-2222-2222-2222-222222222222"
#: The tenant id Entra gives every personal Microsoft account.
MSA_TENANT = "9188040d-6c67-4c5b-b112-36a304b66dad"


@pytest.fixture
def oidc_config():
    return OIDCConfig(client_id="test-client-id", wk_url=WK_URL)


@pytest.fixture
def settings(oidc_config):
    """Real settings, not a Mock.

    Possible only because `AuthSettings.__init__` no longer overwrites its own
    `oidc` argument (docs/adr/0006-microsoft-entra-oidc.md S1.2 gap 8).
    """
    return AuthSettings(method="oidc", oidc=oidc_config)


@pytest.fixture
def auth(settings):
    """An OIDCAuth whose discovery document is pre-seeded, so no network."""
    instance = OIDCAuth(settings=settings, store=Mock())
    instance._config_cache = dict(DISCOVERY)
    instance._config_fetched_at = time.monotonic()
    return instance


@pytest.fixture
def mock_key():
    key = Mock()
    key.verify = Mock()
    return key


def create_mock_token(payload: dict, alg: str = "RS256") -> str:
    header = {"alg": alg, "typ": "JWT", "kid": "test-key"}
    header_b64 = (
        base64.urlsafe_b64encode(json.dumps(header).encode()).decode().rstrip("=")
    )
    payload_b64 = (
        base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    )
    signature_b64 = "mock_signature"
    return f"{header_b64}.{payload_b64}.{signature_b64}"


def _payload(**overrides) -> dict:
    base = {
        "iss": ISSUER,
        "sub": "user-1",
        "aud": ["test-client-id"],
        "exp": time.time() + 3600,
        "iat": time.time() - 10,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Audience
# ---------------------------------------------------------------------------


def test_oidc_auth_audience_as_array(auth, mock_key):
    auth._verify_token(
        create_mock_token(_payload(aud=["test-client-id", "other"])), mock_key
    )


def test_oidc_auth_audience_as_string(auth, mock_key):
    auth._verify_token(
        create_mock_token(_payload(aud="test-client-id other")), mock_key
    )


def test_oidc_auth_audience_from_azp(auth, mock_key):
    auth._verify_token(
        create_mock_token(_payload(aud=["someone-else"], azp="test-client-id")),
        mock_key,
    )


def test_oidc_auth_audience_invalid(auth, mock_key):
    with pytest.raises(Exception, match="Invalid audience"):
        auth._verify_token(
            create_mock_token(_payload(aud=["wrong-client-id"])), mock_key
        )


def test_oidc_auth_accepts_a_configured_extra_audience(settings, mock_key):
    """Entra access tokens are audienced at the API's app ID URI, not the client."""
    settings.oidc.audiences = ["api://test-client-id"]
    auth = OIDCAuth(settings=settings, store=Mock())
    auth._config_cache = dict(DISCOVERY)
    auth._config_fetched_at = time.monotonic()

    auth._verify_token(
        create_mock_token(_payload(aud="api://test-client-id")), mock_key
    )


# ---------------------------------------------------------------------------
# Issuer, algorithm, expiry
# ---------------------------------------------------------------------------


def test_rejects_a_token_from_another_issuer(auth, mock_key):
    with pytest.raises(Exception, match="Invalid issuer"):
        auth._verify_token(
            create_mock_token(_payload(iss="https://evil.example")), mock_key
        )


def test_rejects_a_non_rs256_algorithm(auth, mock_key):
    """The signature check is hardcoded to RS256, so the header must agree."""
    with pytest.raises(Exception, match="Unsupported token algorithm"):
        auth._verify_token(create_mock_token(_payload(), alg="HS256"), mock_key)

    mock_key.verify.assert_not_called()


def test_rejects_a_token_with_no_exp(auth, mock_key):
    payload = _payload()
    del payload["exp"]
    with pytest.raises(Exception, match="no 'exp' claim"):
        auth._verify_token(create_mock_token(payload), mock_key)


def test_rejects_an_expired_token(auth, mock_key):
    with pytest.raises(Exception, match="Token expired"):
        auth._verify_token(
            create_mock_token(_payload(exp=time.time() - 3600)), mock_key
        )


def test_rejects_a_token_that_is_not_yet_valid(auth, mock_key):
    with pytest.raises(Exception, match="not yet valid"):
        auth._verify_token(
            create_mock_token(_payload(nbf=time.time() + 3600)), mock_key
        )


def test_tolerates_clock_skew(auth, mock_key):
    """A few seconds either side must not reject an otherwise valid token."""
    auth._verify_token(create_mock_token(_payload(exp=time.time() - 5)), mock_key)
    auth._verify_token(create_mock_token(_payload(nbf=time.time() + 5)), mock_key)


# ---------------------------------------------------------------------------
# JWKS rotation -- the production blocker (ADR 0006 S1.2 gap 1)
# ---------------------------------------------------------------------------


def _jwk(kid: str) -> dict:
    return {
        "kid": kid,
        "kty": "RSA",
        "n": base64.urlsafe_b64encode((123456789).to_bytes(64, "big"))
        .decode()
        .rstrip("="),
        "e": base64.urlsafe_b64encode((65537).to_bytes(3, "big")).decode().rstrip("="),
    }


def _patch_jwks(*responses):
    """Patch httpx so each successive JWKS fetch returns the next key set."""
    client = MagicMock()
    client.__enter__.return_value = client
    client.get.side_effect = [
        MagicMock(json=MagicMock(return_value=r)) for r in responses
    ]
    httpx = MagicMock()
    httpx.Client.return_value = client
    return patch("titiler.openeo.auth.httpx", httpx), client


def test_unknown_kid_triggers_exactly_one_refetch(auth):
    """A key minted after the cache was filled must not fail forever."""
    patcher, client = _patch_jwks({"keys": [_jwk("old")]}, {"keys": [_jwk("new")]})
    with patcher:
        assert auth._get_key("new") is not None

    assert client.get.call_count == 2


def test_a_known_kid_does_not_refetch(auth):
    patcher, client = _patch_jwks({"keys": [_jwk("old")]})
    with patcher:
        assert auth._get_key("old") is not None

    assert client.get.call_count == 1


def test_a_still_unknown_kid_is_rejected_after_the_refetch(auth):
    patcher, client = _patch_jwks({"keys": [_jwk("old")]}, {"keys": [_jwk("other")]})
    with patcher:
        with pytest.raises(Exception, match="Unable to find appropriate key"):
            auth._get_key("nope")

    assert client.get.call_count == 2


def test_the_refetch_is_rate_limited(auth):
    """A flood of junk kids must not become a flood of provider requests."""
    patcher, client = _patch_jwks(
        {"keys": [_jwk("old")]}, {"keys": [_jwk("old")]}, {"keys": [_jwk("old")]}
    )
    with patcher:
        for _ in range(3):
            with pytest.raises(Exception, match="Unable to find appropriate key"):
                auth._get_key("junk")

    # One initial fetch, one refetch; every later refresh is inside the cooldown.
    assert client.get.call_count == 2


# ---------------------------------------------------------------------------
# Discovery document
# ---------------------------------------------------------------------------


def test_a_templated_issuer_is_accepted(settings):
    """Entra's multi-tenant `common` endpoint returns this (ADR 0006 S2.3)."""
    auth = OIDCAuth(settings=settings, store=Mock())
    templated = {"issuer": ENTRA_TEMPLATE, "jwks_uri": JWKS_URI}

    client = MagicMock()
    client.__enter__.return_value = client
    client.get.return_value = MagicMock(json=MagicMock(return_value=templated))
    httpx = MagicMock()
    httpx.Client.return_value = client

    with patch("titiler.openeo.auth.httpx", httpx):
        assert auth.config["issuer"] == ENTRA_TEMPLATE


def test_discovery_document_is_cached(auth):
    """Pre-seeded by the fixture; reading it again must not refetch."""
    httpx = MagicMock()
    with patch("titiler.openeo.auth.httpx", httpx):
        assert auth.config["issuer"] == ISSUER
        assert auth.config["issuer"] == ISSUER

    httpx.Client.assert_not_called()


# ---------------------------------------------------------------------------
# User identity
# ---------------------------------------------------------------------------


def test_user_id_comes_from_the_configured_claim(settings, mock_key):
    settings.oidc.user_id_claim = "oid"
    store = Mock()
    auth = OIDCAuth(settings=settings, store=store)
    auth._config_cache = dict(DISCOVERY)
    auth._config_fetched_at = time.monotonic()

    token = create_mock_token(_payload(oid="entra-object-id", name="Alice"))
    with patch.object(OIDCAuth, "_get_key", return_value=mock_key):
        user = auth.validate(f"oidc/oidc/{token}")

    assert user.user_id == "entra-object-id"
    store.track_user_login.assert_called_once()


def test_user_id_defaults_to_sub(auth, mock_key):
    token = create_mock_token(_payload(sub="subject-1"))
    with patch.object(OIDCAuth, "_get_key", return_value=mock_key):
        user = auth.validate(f"oidc/oidc/{token}")

    assert user.user_id == "subject-1"


# ---------------------------------------------------------------------------
# Microsoft Entra multi-tenant (`common`)
# ---------------------------------------------------------------------------


def _entra_iss(tid: str) -> str:
    return ENTRA_TEMPLATE.replace("{tenantid}", tid)


def _entra_auth(key_issuer=ENTRA_TEMPLATE, store=None, **oidc) -> OIDCAuth:
    """An OIDCAuth on the `common` endpoint, with no network.

    The cached JWKS binds the test key to `key_issuer`, as Entra's own
    `common/discovery/v2.0/keys` does: `{tenantid}` for the keys any tenant
    may use, the MSA tenant for keys reserved to personal accounts.
    """
    config = OIDCConfig(client_id="test-client-id", wk_url=ENTRA_COMMON_WK_URL, **oidc)
    auth = OIDCAuth(
        settings=AuthSettings(method="oidc", oidc=config), store=store or Mock()
    )
    auth._config_cache = {"issuer": ENTRA_TEMPLATE, "jwks_uri": JWKS_URI}
    auth._config_fetched_at = time.monotonic()
    auth._jwks_cache = {"keys": [{"kid": "test-key", "issuer": key_issuer}]}
    return auth


def _validate(auth: OIDCAuth, mock_key, **claims):
    token = create_mock_token(_payload(**claims))
    with patch.object(OIDCAuth, "_get_key", return_value=mock_key):
        return auth.validate(f"oidc/oidc/{token}")


def test_common_accepts_a_work_account_token(mock_key):
    user = _validate(
        _entra_auth(), mock_key, iss=_entra_iss(TENANT_A), tid=TENANT_A, sub="w-1"
    )
    assert user.user_id == "w-1"


def test_common_accepts_a_personal_account_token(mock_key):
    """MSA tokens are signed by keys the JWKS binds to the MSA tenant."""
    user = _validate(
        _entra_auth(key_issuer=_entra_iss(MSA_TENANT)),
        mock_key,
        iss=_entra_iss(MSA_TENANT),
        tid=MSA_TENANT,
        sub="p-1",
    )
    assert user.user_id == "p-1"


def test_common_rejects_a_tid_iss_mismatch(mock_key):
    with pytest.raises(HTTPException, match="Invalid issuer"):
        _validate(_entra_auth(), mock_key, iss=_entra_iss(TENANT_A), tid=TENANT_B)


def test_common_rejects_a_token_without_tid(mock_key):
    with pytest.raises(HTTPException, match="'tid'"):
        _validate(_entra_auth(), mock_key, iss=_entra_iss(TENANT_A))


@pytest.mark.parametrize("tid", ["not-a-guid", f"{TENANT_A}/v2.0/x", 42])
def test_common_rejects_a_malformed_tid(mock_key, tid):
    """`tid` fills one path segment of the issuer, and nothing else."""
    with pytest.raises(HTTPException, match="'tid'"):
        _validate(_entra_auth(), mock_key, iss=_entra_iss(TENANT_A), tid=tid)


def test_common_rejects_a_v1_issuer(mock_key):
    with pytest.raises(HTTPException, match="requestedAccessTokenVersion"):
        _validate(
            _entra_auth(),
            mock_key,
            iss=f"https://sts.windows.net/{TENANT_A}/",
            tid=TENANT_A,
        )


def test_common_rejects_a_work_token_signed_by_a_personal_account_key(mock_key):
    """The key's own `issuer` is checked, not only the discovery issuer."""
    with pytest.raises(HTTPException, match="signing key"):
        _validate(
            _entra_auth(key_issuer=_entra_iss(MSA_TENANT)),
            mock_key,
            iss=_entra_iss(TENANT_A),
            tid=TENANT_A,
        )


def test_allowed_tenants_rejects_another_tenant(mock_key):
    auth = _entra_auth(allowed_tenants=[TENANT_A, MSA_TENANT])
    with pytest.raises(HTTPException, match="Tenant not allowed"):
        _validate(auth, mock_key, iss=_entra_iss(TENANT_B), tid=TENANT_B)


def test_allowed_tenants_accepts_listed_tenants(mock_key):
    auth = _entra_auth(allowed_tenants=[TENANT_A.upper()])
    _validate(auth, mock_key, iss=_entra_iss(TENANT_A), tid=TENANT_A)


def test_allowed_tenants_rejects_a_token_without_tid(auth, mock_key):
    """Set on a non-Entra provider, the list rejects tokens that carry no tid."""
    auth._oidc_config.allowed_tenants = [TENANT_A]
    with pytest.raises(Exception, match="Tenant not allowed"):
        auth._verify_token(create_mock_token(_payload()), mock_key)


def test_single_tenant_entra_is_unchanged(mock_key):
    """A concrete issuer is compared exactly, and its keys carry that issuer."""
    iss = _entra_iss(TENANT_A)
    auth = _entra_auth(key_issuer=iss)
    auth._config_cache["issuer"] = iss

    _validate(auth, mock_key, iss=iss, tid=TENANT_A)
    with pytest.raises(HTTPException, match="Invalid issuer"):
        _validate(auth, mock_key, iss=_entra_iss(TENANT_B), tid=TENANT_B)


def test_a_key_without_an_issuer_is_not_checked(auth, mock_key):
    """Keycloak JWKS entries carry no `issuer`."""
    auth._jwks_cache = {"keys": [{"kid": "test-key"}]}
    token = create_mock_token(_payload())
    with patch.object(OIDCAuth, "_get_key", return_value=mock_key):
        assert auth.validate(f"oidc/oidc/{token}").user_id == "user-1"


def test_authority_comes_from_the_well_known_url():
    assert _entra_auth().authority == "https://login.microsoftonline.com/common/v2.0"


def test_authority_falls_back_to_the_discovery_issuer(settings):
    settings.oidc.wk_url = "https://auth.example.com/discovery"
    auth = OIDCAuth(settings=settings, store=Mock())
    auth._config_cache = dict(DISCOVERY)
    auth._config_fetched_at = time.monotonic()
    assert auth.authority == ISSUER


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


def test_auth_settings_no_longer_clobbers_its_oidc_argument():
    config = OIDCConfig(client_id="mine", wk_url=WK_URL)
    assert AuthSettings(method="oidc", oidc=config).oidc is config


def test_oidc_method_requires_client_id_and_wk_url():
    with pytest.raises(ValidationError, match="TITILER_OPENEO_AUTH_OIDC_WK_URL"):
        AuthSettings(method="oidc", oidc=OIDCConfig(client_id="mine"))

    with pytest.raises(ValidationError, match="TITILER_OPENEO_AUTH_OIDC_CLIENT_ID"):
        AuthSettings(method="oidc", oidc=OIDCConfig(wk_url=WK_URL))


def test_basic_method_needs_no_oidc_config():
    assert AuthSettings(method="basic").method == "basic"


def test_redirect_url_accepts_one_url_or_a_list():
    assert OIDCConfig(redirect_url="http://a/").redirect_url == ["http://a/"]
    assert OIDCConfig(redirect_url="http://a/ http://b/").redirect_url == [
        "http://a/",
        "http://b/",
    ]
    assert OIDCConfig(redirect_url=["http://a/"]).redirect_url == ["http://a/"]
    assert OIDCConfig().redirect_url == []


def test_list_settings_parse_space_separated_env(monkeypatch):
    monkeypatch.setenv("TITILER_OPENEO_AUTH_OIDC_REDIRECT_URL", "http://a/ http://b/")
    monkeypatch.setenv(
        "TITILER_OPENEO_AUTH_OIDC_ALLOWED_TENANTS", f"{TENANT_A} {MSA_TENANT}"
    )
    config = OIDCConfig()
    assert config.redirect_url == ["http://a/", "http://b/"]
    assert config.allowed_tenants == [TENANT_A, MSA_TENANT]


def test_single_redirect_url_env_stays_valid(monkeypatch):
    monkeypatch.setenv(
        "TITILER_OPENEO_AUTH_OIDC_REDIRECT_URL", "http://localhost:8080/"
    )
    monkeypatch.delenv("TITILER_OPENEO_AUTH_OIDC_ALLOWED_TENANTS", raising=False)
    config = OIDCConfig()
    assert config.redirect_url == ["http://localhost:8080/"]
    assert config.allowed_tenants == []


def test_audiences_parse_space_separated():
    config = OIDCConfig(client_id="c", wk_url=WK_URL, audiences=["a", "b"])
    assert config.audiences == ["a", "b"]


# ---------------------------------------------------------------------------
# AuthToken
# ---------------------------------------------------------------------------


def test_auth_token_empty_string_validation():
    """Test that empty token string raises proper validation error."""
    with pytest.raises((ValidationError, ValueError)):
        AuthToken(method="oidc", provider="realm", token="")


def test_auth_token_valid():
    """Test that valid token is accepted."""
    token = AuthToken(method="oidc", provider="realm", token="valid_token_123")
    assert token.token == "valid_token_123"
    assert token.method == "oidc"
    assert token.provider == "realm"


def test_auth_token_from_token():
    """Test parsing Bearer token format."""
    bearer = "Bearer oidc/realm/my_access_token"
    token = AuthToken.from_token(bearer)
    assert token.method == "oidc"
    assert token.provider == "realm"
    assert token.token == "my_access_token"


# ---------------------------------------------------------------------------
# Error reporting
# ---------------------------------------------------------------------------


def test_a_bad_signature_reports_a_usable_message(auth):
    """`str(InvalidSignature())` is empty, which used to surface as "401:"."""
    from cryptography.exceptions import InvalidSignature

    key = Mock()
    key.verify = Mock(side_effect=InvalidSignature())

    with pytest.raises(Exception) as excinfo:
        auth._verify_token(create_mock_token(_payload()), key)

    message = str(excinfo.value)
    assert "signature verification failed" in message
    # The token's own facts, so the cause is readable off the error.
    assert "kid='test-key'" in message
    assert "nonce_in_header=False" in message


def test_a_nonced_header_is_named_as_the_cause(auth):
    """Entra signs its own-resource tokens over a header whose `nonce` has been
    replaced by its SHA-256, so a correct key still cannot verify them."""
    explanation = OIDCAuth._explain_bad_signature(
        {"alg": "RS256", "kid": "k1", "nonce": "abc"},
        {
            "aud": "00000003-0000-0000-c000-000000000000",
            "iss": "https://sts.windows.net/t/",
        },
    )
    assert "`nonce` in its JWT header" in explanation
    assert "by design and not a misconfiguration" in explanation
    assert "nonce_in_header=True" in explanation


def test_a_plain_bad_signature_does_not_blame_entra(auth):
    """Without a nonce the honest answer is "the key did not verify it"."""
    explanation = OIDCAuth._explain_bad_signature(
        {"alg": "RS256", "kid": "k1"}, {"aud": "client", "iss": "https://idp/"}
    )
    assert "nonce" not in explanation.split("Token facts:")[0]
    assert "did not verify this token" in explanation


def test_the_explanation_never_echoes_the_signature(auth):
    """It is built from header/payload only -- no token material."""
    explanation = OIDCAuth._explain_bad_signature(
        {"alg": "RS256", "kid": "k1", "nonce": "s3cret-nonce"},
        {"aud": "a", "iss": "b", "appid": "c"},
    )
    assert "s3cret-nonce" not in explanation


def test_validate_does_not_double_wrap_an_http_exception(auth, mock_key):
    """Re-wrapping produced details like "401: 401:", hiding the real cause."""
    from fastapi.exceptions import HTTPException

    with patch.object(
        OIDCAuth, "_get_key", side_effect=HTTPException(401, "Unable to find key")
    ):
        with pytest.raises(HTTPException) as excinfo:
            auth.validate(f"oidc/oidc/{create_mock_token(_payload())}")

    assert excinfo.value.detail == "Unable to find key"


def test_a_blank_exception_still_yields_a_named_detail(auth, mock_key):
    class Blank(Exception):
        def __str__(self):
            return ""

    with patch.object(OIDCAuth, "_get_key", side_effect=Blank()):
        with pytest.raises(Exception) as excinfo:
            auth.validate(f"oidc/oidc/{create_mock_token(_payload())}")

    assert "Blank during token validation" in str(excinfo.value)


# ---------------------------------------------------------------------------
# Store failures
# ---------------------------------------------------------------------------


def test_store_failure_during_login_tracking_is_a_503(auth, mock_key):
    """A database outage must not tell the client its valid token is bad."""
    from titiler.openeo.errors import ServiceUnavailable

    auth.store.track_user_login.side_effect = RuntimeError(
        "connection to server at db.internal (10.0.0.5) failed"
    )
    token = create_mock_token(_payload(sub="subject-1"))
    with patch.object(OIDCAuth, "_get_key", return_value=mock_key):
        with pytest.raises(ServiceUnavailable) as excinfo:
            auth.validate(f"oidc/oidc/{token}")

    assert excinfo.value.status_code == 503
    assert excinfo.value.headers == {"Retry-After": "5"}
    assert "10.0.0.5" not in excinfo.value.message
