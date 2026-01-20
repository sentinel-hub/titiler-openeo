"""Tests for authentication module."""

import base64
import json
from unittest.mock import Mock, patch

import pytest

from titiler.openeo.auth import OIDCAuth, OIDCConfig


@pytest.fixture
def oidc_config():
    return OIDCConfig(
        issuer="https://auth.example.com",
        client_id="test-client-id",
        jwks_uri="https://auth.example.com/jwks",
    )


@pytest.fixture
def mock_settings(oidc_config):
    settings = Mock()
    settings.oidc = oidc_config
    return settings


def create_mock_token(payload: dict) -> str:
    header = {"alg": "RS256", "typ": "JWT", "kid": "test-key"}
    header_b64 = (
        base64.urlsafe_b64encode(json.dumps(header).encode()).decode().rstrip("=")
    )
    payload_b64 = (
        base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    )
    signature_b64 = "mock_signature"
    return f"{header_b64}.{payload_b64}.{signature_b64}"


@patch.object(OIDCAuth, "_get_key")
def test_oidc_auth_audience_as_array(mock_get_key, mock_settings):
    mock_key = Mock()
    mock_key.verify = Mock()
    mock_get_key.return_value = mock_key

    mock_store = Mock()
    auth = OIDCAuth(settings=mock_settings, store=mock_store)

    payload = {
        "aud": ["test-client-id", "other-audience"],
        "exp": 9999999999,
        "iat": 1000000000,
    }
    token = create_mock_token(payload)
    auth._verify_token(token, mock_key)


@patch.object(OIDCAuth, "_get_key")
def test_oidc_auth_audience_as_string(mock_get_key, mock_settings):
    mock_key = Mock()
    mock_key.verify = Mock()
    mock_get_key.return_value = mock_key

    mock_store = Mock()
    auth = OIDCAuth(settings=mock_settings, store=mock_store)

    payload = {
        "aud": "test-client-id other-audience",
        "exp": 9999999999,
        "iat": 1000000000,
    }
    token = create_mock_token(payload)
    auth._verify_token(token, mock_key)


@patch.object(OIDCAuth, "_get_key")
def test_oidc_auth_audience_invalid(mock_get_key, mock_settings):
    mock_key = Mock()
    mock_key.verify = Mock()
    mock_get_key.return_value = mock_key

    mock_store = Mock()
    auth = OIDCAuth(settings=mock_settings, store=mock_store)

    payload = {
        "aud": ["wrong-client-id"],
        "exp": 9999999999,
        "iat": 1000000000,
    }
    token = create_mock_token(payload)

    with pytest.raises(Exception, match="Invalid audience"):
        auth._verify_token(token, mock_key)
