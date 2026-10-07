"""Tests for `GET /credentials/oidc`."""

import time
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from titiler.openeo.auth import OIDCAuth
from titiler.openeo.factory import EndpointsFactory
from titiler.openeo.settings import AuthSettings, OIDCConfig

ENTRA_COMMON_WK_URL = (
    "https://login.microsoftonline.com/common/v2.0/.well-known/openid-configuration"
)
ENTRA_TEMPLATE = "https://login.microsoftonline.com/{tenantid}/v2.0"


def _client(redirect_url="", **oidc) -> TestClient:
    config = OIDCConfig(
        client_id="test-client-id",
        wk_url=ENTRA_COMMON_WK_URL,
        redirect_url=redirect_url,
        **oidc,
    )
    auth = OIDCAuth(settings=AuthSettings(method="oidc", oidc=config), store=Mock())
    # The discovery document `common` really returns; no network.
    auth._config_cache = {"issuer": ENTRA_TEMPLATE, "jwks_uri": "unused"}
    auth._config_fetched_at = time.monotonic()

    endpoints = EndpointsFactory(
        services_store=Mock(),
        udp_store=Mock(),
        stac_client=Mock(),
        process_registry=Mock(),
        auth=auth,
    )
    app = FastAPI()
    app.include_router(endpoints.router)
    return TestClient(app)


def _provider(client: TestClient) -> dict:
    response = client.get("/credentials/oidc")
    assert response.status_code == 200, response.text
    (provider,) = response.json()["providers"]
    return provider


def test_issuer_is_the_authority_not_the_templated_issuer():
    provider = _provider(_client())
    assert provider["issuer"] == "https://login.microsoftonline.com/common/v2.0"


@pytest.mark.parametrize(
    "redirect_url, expected",
    [
        ("http://localhost:8080/", ["http://localhost:8080/"]),
        (
            "http://localhost:8080/ https://editor.openeo.org/",
            ["http://localhost:8080/", "https://editor.openeo.org/"],
        ),
    ],
)
def test_redirect_urls_lists_every_configured_url(redirect_url, expected):
    (client,) = _provider(_client(redirect_url=redirect_url))["default_clients"]
    assert client["redirect_urls"] == expected


def test_no_redirect_url_omits_redirect_urls():
    """Previously advertised `[""]`, which is not a valid URL."""
    (client,) = _provider(_client())["default_clients"]
    assert "redirect_urls" not in client
