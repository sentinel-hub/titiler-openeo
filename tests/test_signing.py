"""Tests for titiler.openeo.signing (docs/adr/0005-asset-href-signing.md)."""

import json
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pystac
import pytest

from titiler.openeo import signing
from titiler.openeo.settings import PlanetaryComputerSettings
from titiler.openeo.signing import (
    PLANETARY_COMPUTER,
    PlanetaryComputerSigner,
    SigningError,
    get_href_signer,
    rules_for_catalogue,
)

FIXTURES = Path(__file__).parent / "fixtures"

PC_STAC_API = "https://planetarycomputer.microsoft.com/api/stac/v1"
BLOB_HREF = "https://sentinel2l2a01.blob.core.windows.net/sentinel2-l2/48/X/VR/2026/08/10/B04.tif"
TILEJSON_HREF = (
    "https://planetarycomputer.microsoft.com/api/data/v1/item/tilejson.json"
    "?collection=sentinel-2-l2a"
)


@pytest.fixture(autouse=True)
def clear_token_cache():
    """The token cache is module-level; keep tests independent of each other."""
    signing._TOKEN_CACHE.clear()
    yield
    signing._TOKEN_CACHE.clear()


def _sas_response(token: str = "st=X&se=Y&sig=abc%3D", minutes: int = 45):
    """A mock urlopen context manager returning one SAS API payload."""
    expiry = datetime.now(timezone.utc) + timedelta(minutes=minutes)
    payload = json.dumps(
        {
            "msft:expiry": expiry.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "token": token,
        }
    ).encode()

    response = MagicMock()
    response.read.return_value = payload
    ctx = MagicMock()
    ctx.__enter__.return_value = response
    return ctx


# ---------------------------------------------------------------------------
# Activation (ADR 0005 S2.3)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        PC_STAC_API,
        "https://planetarycomputer.microsoft.com/api/stac/v1/",
        "https://PLANETARYCOMPUTER.microsoft.com/api/stac/v1",
    ],
)
def test_rules_for_catalogue_matches_planetary_computer(url):
    assert rules_for_catalogue(url) == (PLANETARY_COMPUTER,)


@pytest.mark.parametrize(
    "url",
    [
        "https://stac.dataspace.copernicus.eu/v1",
        "https://earth-search.aws.element84.com/v1",
        "https://stac.eoapi.dev",
        "https://planetarycomputer.microsoft.com.evil.example/api/stac/v1",
        "",
    ],
)
def test_rules_for_catalogue_ignores_other_catalogues(url):
    assert rules_for_catalogue(url) == ()


def test_no_rules_means_no_signer():
    """The gate: no rules -> None, so call sites run their unchanged path."""
    assert get_href_signer(()) is None
    assert get_href_signer([]) is None


# ---------------------------------------------------------------------------
# Host gating
# ---------------------------------------------------------------------------


def test_signs_blob_href():
    with patch("titiler.openeo.signing.urllib.request.urlopen") as urlopen:
        urlopen.return_value = _sas_response(token="st=A&se=B&sig=zzz")
        signer = get_href_signer(rules_for_catalogue(PC_STAC_API))
        signed = signer(BLOB_HREF)

    assert signed == f"{BLOB_HREF}?st=A&se=B&sig=zzz"


def test_leaves_non_blob_hosts_untouched():
    """PC's own tilejson/rendered_preview assets are not on blob storage."""
    with patch("titiler.openeo.signing.urllib.request.urlopen") as urlopen:
        signer = get_href_signer(rules_for_catalogue(PC_STAC_API))
        assert signer(TILEJSON_HREF) == TILEJSON_HREF
        assert (
            signer("https://sentinel-cogs.s3.us-west-2.amazonaws.com/x.tif")
            == "https://sentinel-cogs.s3.us-west-2.amazonaws.com/x.tif"
        )
        assert signer("s3://eodata/x.tif") == "s3://eodata/x.tif"

    urlopen.assert_not_called()


def test_signing_is_idempotent():
    """An already-signed href is never given a second token."""
    already = f"{BLOB_HREF}?st=A&se=B&sig=existing"
    with patch("titiler.openeo.signing.urllib.request.urlopen") as urlopen:
        signer = get_href_signer(rules_for_catalogue(PC_STAC_API))
        assert signer(already) == already

    urlopen.assert_not_called()


def test_preserves_an_existing_query_string():
    with patch("titiler.openeo.signing.urllib.request.urlopen") as urlopen:
        urlopen.return_value = _sas_response(token="sig=zzz")
        signer = get_href_signer(rules_for_catalogue(PC_STAC_API))
        signed = signer(f"{BLOB_HREF}?versionid=2")

    assert signed == f"{BLOB_HREF}?versionid=2&sig=zzz"


def test_blob_url_without_a_container_is_untouched():
    href = "https://sentinel2l2a01.blob.core.windows.net/"
    with patch("titiler.openeo.signing.urllib.request.urlopen") as urlopen:
        signer = get_href_signer(rules_for_catalogue(PC_STAC_API))
        assert signer(href) == href

    urlopen.assert_not_called()


# ---------------------------------------------------------------------------
# Token cache (ADR 0005 S2.4)
# ---------------------------------------------------------------------------


def test_one_token_is_minted_per_container():
    signer = PlanetaryComputerSigner()
    with patch("titiler.openeo.signing.urllib.request.urlopen") as urlopen:
        urlopen.return_value = _sas_response()
        signer("https://acct.blob.core.windows.net/cont/a.tif")
        signer("https://acct.blob.core.windows.net/cont/b.tif")
        signer("https://acct.blob.core.windows.net/cont/c.tif")

    assert urlopen.call_count == 1


def test_separate_containers_and_accounts_get_separate_tokens():
    signer = PlanetaryComputerSigner()
    with patch("titiler.openeo.signing.urllib.request.urlopen") as urlopen:
        urlopen.return_value = _sas_response()
        signer("https://acct.blob.core.windows.net/one/a.tif")
        signer("https://acct.blob.core.windows.net/two/a.tif")
        signer("https://other.blob.core.windows.net/one/a.tif")

    assert urlopen.call_count == 3
    assert set(signing._TOKEN_CACHE) == {
        ("acct", "one"),
        ("acct", "two"),
        ("other", "one"),
    }


def test_token_is_reminted_inside_the_expiry_margin():
    """A token expiring sooner than the margin must not be handed out."""
    settings = PlanetaryComputerSettings(expiry_margin=300.0)
    signer = PlanetaryComputerSigner(settings=settings)

    with patch("titiler.openeo.signing.urllib.request.urlopen") as urlopen:
        # Expires in 2 minutes -- inside the 5 minute margin.
        urlopen.return_value = _sas_response(minutes=2)
        signer(BLOB_HREF)
        signer(BLOB_HREF)

    assert urlopen.call_count == 2


def test_token_is_reused_outside_the_expiry_margin():
    settings = PlanetaryComputerSettings(expiry_margin=300.0)
    signer = PlanetaryComputerSigner(settings=settings)

    with patch("titiler.openeo.signing.urllib.request.urlopen") as urlopen:
        urlopen.return_value = _sas_response(minutes=45)
        signer(BLOB_HREF)
        signer(BLOB_HREF)

    assert urlopen.call_count == 1


def test_cache_is_shared_across_users():
    """Public PC tokens are identity-blind, so one entry per container is right."""
    from titiler.openeo.models.auth import User

    rules = rules_for_catalogue(PC_STAC_API)
    with patch("titiler.openeo.signing.urllib.request.urlopen") as urlopen:
        urlopen.return_value = _sas_response()
        get_href_signer(rules, User(user_id="alice"))(BLOB_HREF)
        get_href_signer(rules, User(user_id="bob"))(BLOB_HREF)

    assert urlopen.call_count == 1


# ---------------------------------------------------------------------------
# Requests and failures
# ---------------------------------------------------------------------------


def test_uses_the_account_container_token_endpoint():
    signer = PlanetaryComputerSigner()
    with patch("titiler.openeo.signing.urllib.request.urlopen") as urlopen:
        urlopen.return_value = _sas_response()
        signer(BLOB_HREF)

    request = urlopen.call_args[0][0]
    assert request.full_url == (
        "https://planetarycomputer.microsoft.com/api/sas/v1"
        "/token/sentinel2l2a01/sentinel2-l2"
    )
    assert "Ocp-apim-subscription-key" not in request.headers


def test_sends_the_subscription_key_when_configured():
    settings = PlanetaryComputerSettings(subscription_key="secret")
    signer = PlanetaryComputerSigner(settings=settings)
    with patch("titiler.openeo.signing.urllib.request.urlopen") as urlopen:
        urlopen.return_value = _sas_response()
        signer(BLOB_HREF)

    # urllib title-cases header names.
    assert urlopen.call_args[0][0].headers["Ocp-apim-subscription-key"] == "secret"


def test_a_transient_failure_is_retried_once():
    signer = PlanetaryComputerSigner()
    with patch("titiler.openeo.signing.urllib.request.urlopen") as urlopen:
        urlopen.side_effect = [urllib.error.URLError("boom"), _sas_response()]
        assert signer(BLOB_HREF).endswith("sig=abc%3D")

    assert urlopen.call_count == 2


def test_a_persistent_failure_raises_rather_than_returning_an_unsigned_href():
    signer = PlanetaryComputerSigner()
    with patch("titiler.openeo.signing.urllib.request.urlopen") as urlopen:
        urlopen.side_effect = urllib.error.URLError("down")
        with pytest.raises(SigningError, match="sentinel2l2a01/sentinel2-l2"):
            signer(BLOB_HREF)

    assert urlopen.call_count == 2


def test_a_malformed_payload_raises():
    signer = PlanetaryComputerSigner()
    response = MagicMock()
    response.read.return_value = b'{"unexpected": true}'
    ctx = MagicMock()
    ctx.__enter__.return_value = response

    with patch("titiler.openeo.signing.urllib.request.urlopen") as urlopen:
        urlopen.return_value = ctx
        with pytest.raises(SigningError):
            signer(BLOB_HREF)


# ---------------------------------------------------------------------------
# Against the committed Planetary Computer fixtures
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "fixture",
    [
        pytest.param("sentinel2/items/planetary_computer.json", id="Sentinel2"),
        pytest.param("sar/items/planetary_computer.json", id="SAR"),
    ],
)
def test_signs_every_blob_asset_of_a_real_item(fixture):
    """Both fixtures are trimmed to blob-hosted assets, so all of them sign.

    The API-hosted assets a live item also carries (`tilejson`,
    `rendered_preview`) were trimmed out of these fixtures; that they stay
    unsigned is covered by `test_leaves_non_blob_hosts_untouched`.
    """
    item = pystac.Item.from_dict(json.loads((FIXTURES / fixture).read_text()))

    with patch("titiler.openeo.signing.urllib.request.urlopen") as urlopen:
        urlopen.return_value = _sas_response(token="sig=tok")
        signer = get_href_signer(rules_for_catalogue(PC_STAC_API))
        results = {
            key: signer(asset.get_absolute_href() or asset.href)
            for key, asset in item.assets.items()
        }

    unsigned = {key for key, href in results.items() if not href.endswith("?sig=tok")}
    assert unsigned == set(), results
    assert results, "fixture carries no assets"

    # One token per container, however many assets share it.
    assert urlopen.call_count == 1


def test_sar_annotation_assets_are_signed():
    """The band-source path reads these, and PC serves them from blob storage
    (ADR 0001 S7.6, ADR 0005 S1.2)."""
    item = pystac.Item.from_dict(
        json.loads((FIXTURES / "sar/items/planetary_computer.json").read_text())
    )

    with patch("titiler.openeo.signing.urllib.request.urlopen") as urlopen:
        urlopen.return_value = _sas_response(token="sig=tok")
        signer = get_href_signer(rules_for_catalogue(PC_STAC_API))
        annotation = {
            key: signer(asset.href)
            for key, asset in item.assets.items()
            if key.startswith(("schema-calibration-", "schema-noise-"))
        }

    assert len(annotation) == 4, annotation
    assert all(href.endswith("?sig=tok") for href in annotation.values())


def test_default_signer_is_none_until_rules_are_set():
    signing.set_default_rules(())
    assert signing.get_default_href_signer() is None

    signing.set_default_rules(rules_for_catalogue(PC_STAC_API))
    try:
        assert signing.get_default_href_signer() is not None
    finally:
        signing.set_default_rules(())
