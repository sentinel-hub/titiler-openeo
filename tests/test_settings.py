"""Tests for titiler.openeo.settings."""

import pytest

from titiler.openeo.settings import BackendSettings, PlanetaryComputerSettings

STAC = "https://example.com/stac"
STORE = "services/example.json"


@pytest.fixture
def backend_env(monkeypatch):
    monkeypatch.setenv("TITILER_OPENEO_STAC_API_URL", STAC)
    monkeypatch.setenv("TITILER_OPENEO_STORE_URL", STORE)
    return monkeypatch


# ---------------------------------------------------------------------------
# exclude_collections
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("noaa-c-cap", ["noaa-c-cap"]),
        ("a,b,c", ["a", "b", "c"]),
        ("a, b , c", ["a", "b", "c"]),
        ("a,,b", ["a", "b"]),
        ("", []),
    ],
)
def test_exclude_collections_parses_comma_separated_env(backend_env, raw, expected):
    """Regression: a `list[str]` is "complex" to pydantic-settings, which
    JSON-decoded the environment value before `parse_exclude_collections` could
    run -- so the documented comma-separated form raised `SettingsError` and
    this setting could not be set from the environment at all."""
    backend_env.setenv("TITILER_OPENEO_EXCLUDE_COLLECTIONS", raw)
    assert BackendSettings().exclude_collections == expected


def test_exclude_collections_defaults_to_empty(backend_env):
    backend_env.delenv("TITILER_OPENEO_EXCLUDE_COLLECTIONS", raising=False)
    assert BackendSettings().exclude_collections == []


def test_exclude_collections_accepts_a_list_directly(backend_env):
    assert BackendSettings(exclude_collections=["x", "y"]).exclude_collections == [
        "x",
        "y",
    ]


# ---------------------------------------------------------------------------
# PlanetaryComputerSettings
# ---------------------------------------------------------------------------


def test_planetary_computer_defaults_need_no_configuration():
    settings = PlanetaryComputerSettings()
    assert settings.sas_url == "https://planetarycomputer.microsoft.com/api/sas/v1"
    assert settings.subscription_key == ""
    assert settings.expiry_margin == 300.0
    assert settings.timeout == 10.0


def test_planetary_computer_reads_its_env_prefix(monkeypatch):
    monkeypatch.setenv("TITILER_OPENEO_PC_SUBSCRIPTION_KEY", "secret")
    monkeypatch.setenv("TITILER_OPENEO_PC_EXPIRY_MARGIN", "60")
    settings = PlanetaryComputerSettings()
    assert settings.subscription_key == "secret"
    assert settings.expiry_margin == 60.0
