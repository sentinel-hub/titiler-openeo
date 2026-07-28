"""Tests for titiler.openeo.sar.fetcher."""

import sys

import pytest

from titiler.openeo.sar.fetcher import ObstoreFetcher, get_default_fetcher

# --------------------------------------------------------------------------- dispatch


def test_fetch_dispatches_https_to_http_get(monkeypatch):
    """https:// hrefs go through the plain HTTP path, not obstore."""
    calls = []
    monkeypatch.setattr(
        "titiler.openeo.sar.fetcher._http_get", lambda url: calls.append(url) or b"x"
    )
    fetcher = ObstoreFetcher()
    result = fetcher.fetch("https://example.com/calibration.xml")
    assert result == b"x"
    assert calls == ["https://example.com/calibration.xml"]


def test_fetch_dispatches_http_to_http_get(monkeypatch):
    """http:// (non-TLS) hrefs also go through the plain HTTP path."""
    monkeypatch.setattr("titiler.openeo.sar.fetcher._http_get", lambda url: b"payload")
    fetcher = ObstoreFetcher()
    assert fetcher.fetch("http://example.com/x.xml") == b"payload"


def test_fetch_dispatches_s3_to_fetch_s3(monkeypatch):
    """s3:// hrefs are split into bucket/key and routed to the S3 path."""
    seen = {}

    def fake_fetch_s3(self, bucket, key):
        seen["bucket"] = bucket
        seen["key"] = key
        return b"s3-payload"

    monkeypatch.setattr(ObstoreFetcher, "_fetch_s3", fake_fetch_s3)
    fetcher = ObstoreFetcher()
    result = fetcher.fetch("s3://eodata/Sentinel-1/foo/bar.xml")
    assert result == b"s3-payload"
    assert seen == {"bucket": "eodata", "key": "Sentinel-1/foo/bar.xml"}


def test_fetch_unsupported_scheme_raises():
    """A scheme that is neither http(s) nor s3 raises a clear ValueError."""
    fetcher = ObstoreFetcher()
    with pytest.raises(ValueError, match="ftp"):
        fetcher.fetch("ftp://example.com/x.xml")


def test_get_default_fetcher_returns_singleton():
    """The module-level default fetcher is constructed once and reused."""
    assert get_default_fetcher() is get_default_fetcher()


# --------------------------------------------------------------------------- S3 store option mapping


@pytest.fixture(autouse=True)
def _clean_aws_env(monkeypatch):
    """Ensure no AWS env leaks in from the developer's shell during these tests."""
    for var in (
        "AWS_REGION",
        "AWS_REQUEST_PAYER",
        "AWS_VIRTUAL_HOSTING",
        "AWS_ENDPOINT_URL",
        "AWS_S3_ENDPOINT",
        "AWS_PROFILE",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
    ):
        monkeypatch.delenv(var, raising=False)


def test_s3_store_options_anonymous_by_default():
    """With no AWS env at all, options fall back to anonymous access."""
    opts = ObstoreFetcher()._s3_store_options()
    assert opts["skip_signature"] is True
    assert "endpoint" not in opts  # obstore rejects endpoint=None
    assert "credential_provider" not in opts
    assert "access_key_id" not in opts
    assert opts["region"] == "us-east-1"
    assert opts["request_payer"] is False
    assert opts["virtual_hosted_style_request"] is False


def test_s3_store_options_static_keys(monkeypatch):
    """Static key/secret + GDAL-style endpoint are picked up and normalised."""
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIDEXAMPLE")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "secret")
    monkeypatch.setenv("AWS_S3_ENDPOINT", "eodata.dataspace.copernicus.eu")
    monkeypatch.setenv("AWS_VIRTUAL_HOSTING", "FALSE")

    opts = ObstoreFetcher()._s3_store_options()

    assert opts["access_key_id"] == "AKIDEXAMPLE"
    assert opts["secret_access_key"] == "secret"
    # GDAL's AWS_S3_ENDPOINT is a bare host; obstore needs a full URL.
    assert opts["endpoint"] == "https://eodata.dataspace.copernicus.eu"
    assert opts["virtual_hosted_style_request"] is False
    assert "skip_signature" not in opts
    assert "credential_provider" not in opts


def test_s3_store_options_session_token(monkeypatch):
    """A session token is forwarded alongside static keys when present."""
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIDEXAMPLE")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "secret")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "token-123")

    opts = ObstoreFetcher()._s3_store_options()
    assert opts["token"] == "token-123"


def test_s3_store_options_native_endpoint_url_takes_precedence(monkeypatch):
    """obstore's own AWS_ENDPOINT_URL wins over GDAL's AWS_S3_ENDPOINT."""
    monkeypatch.setenv("AWS_ENDPOINT_URL", "https://native.example.com")
    monkeypatch.setenv("AWS_S3_ENDPOINT", "gdal.example.com")

    opts = ObstoreFetcher()._s3_store_options()
    assert opts["endpoint"] == "https://native.example.com"


def test_s3_store_options_virtual_hosting_true(monkeypatch):
    """AWS_VIRTUAL_HOSTING=TRUE maps to virtual_hosted_style_request=True."""
    monkeypatch.setenv("AWS_VIRTUAL_HOSTING", "TRUE")
    opts = ObstoreFetcher()._s3_store_options()
    assert opts["virtual_hosted_style_request"] is True


def test_s3_store_options_request_payer(monkeypatch):
    """AWS_REQUEST_PAYER=requester maps to request_payer=True."""
    monkeypatch.setenv("AWS_REQUEST_PAYER", "requester")
    opts = ObstoreFetcher()._s3_store_options()
    assert opts["request_payer"] is True


def test_s3_store_options_profile_uses_boto3_credential_provider(monkeypatch):
    """AWS_PROFILE selects the boto3 credential provider, even if static keys are also set.

    boto3.Session(profile_name=...) validates the profile against
    ~/.aws/config at construction, and Boto3CredentialProvider eagerly calls
    session.get_credentials(). Neither can be exercised for real in a unit
    test without a real AWS profile present (which CI does not have), so
    both are faked here -- this test verifies our own wiring (the right
    profile name reaches the right constructor), not boto3/obstore's
    internals.
    """
    monkeypatch.setenv("AWS_PROFILE", "cdse")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "should-be-ignored")

    calls = {}

    class FakeSession:
        def __init__(self, profile_name=None):
            calls["profile_name"] = profile_name

    class FakeProvider:
        def __init__(self, session):
            calls["session"] = session

    import boto3
    import obstore.auth.boto3 as obstore_boto3

    monkeypatch.setattr(boto3, "Session", FakeSession)
    monkeypatch.setattr(obstore_boto3, "Boto3CredentialProvider", FakeProvider)

    opts = ObstoreFetcher()._s3_store_options()

    assert calls["profile_name"] == "cdse"
    assert isinstance(calls["session"], FakeSession)
    assert isinstance(opts["credential_provider"], FakeProvider)
    assert "access_key_id" not in opts


def test_s3_store_options_profile_without_boto3_raises_clear_error(monkeypatch):
    """If AWS_PROFILE is set but boto3 is not installed, the error names the fix."""
    monkeypatch.setenv("AWS_PROFILE", "cdse")
    monkeypatch.setitem(sys.modules, "boto3", None)

    with pytest.raises(ImportError, match=r"titiler-openeo\[boto3\]"):
        ObstoreFetcher()._s3_store_options()
