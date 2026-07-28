"""Asset fetching for Sentinel-1 annotation XML.

The measurement GeoTIFF is read by GDAL/rasterio, which resolves its own S3
credentials from the environment. The calibration/noise annotation XML sitting
next to it is not a raster, so it needs its own client. This module is that
client, kept behind a narrow protocol so the mechanism is swappable and
testable -- see docs/adr/0001-sar-backscatter.md S7.6.
"""

import os
import urllib.request
from typing import Optional, Protocol
from urllib.parse import urlparse

__all__ = ["AssetFetcher", "ObstoreFetcher", "get_default_fetcher"]


class AssetFetcher(Protocol):
    """Fetches a STAC asset's raw bytes, given its href."""

    def fetch(self, href: str) -> bytes:
        """Return the raw bytes for `href`."""
        ...


def _http_get(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=60) as resp:  # noqa: S310
        return resp.read()


class ObstoreFetcher:
    """Default AssetFetcher: `obstore` for s3://, a plain HTTP GET otherwise.

    Two of the three STAC catalogues this project targets serve annotation
    XML over authenticated S3 only (ADR S7.6), so a plain HTTP client is not
    sufficient on its own.

    Credentials come from the environment, mirroring the GDAL/AWS variables
    already documented for the rasterio read path (see .env.cdse) so one
    credential secret can drive both without duplication. Static keys, custom
    endpoints, requester-pays, IRSA, IMDS and ECS container credentials are
    handled by obstore's native (Rust) auth path. `AWS_PROFILE`/SSO
    credentials are not -- support was removed upstream in arrow-rs
    (https://github.com/apache/arrow-rs/pull/4238) -- so when `AWS_PROFILE`
    is set this falls back to `obstore.auth.boto3.Boto3CredentialProvider`,
    which needs the optional `boto3` extra
    (https://github.com/developmentseed/obstore/issues/571).
    """

    def fetch(self, href: str) -> bytes:
        """Fetch `href`, dispatching on URL scheme."""
        parsed = urlparse(href)
        if parsed.scheme in ("http", "https"):
            return _http_get(href)
        if parsed.scheme == "s3":
            return self._fetch_s3(parsed.netloc, parsed.path.lstrip("/"))
        raise ValueError(f"Unsupported asset href scheme: {href!r}")

    def _fetch_s3(self, bucket: str, key: str) -> bytes:
        import obstore
        from obstore.store import S3Store

        store = S3Store(bucket, **self._s3_store_options())
        return bytes(obstore.get(store, key).bytes())

    def _s3_store_options(self) -> dict:
        """Build S3Store kwargs from the environment."""
        opts: dict = {
            "region": os.environ.get("AWS_REGION", "us-east-1"),
            "request_payer": os.environ.get("AWS_REQUEST_PAYER", "").lower()
            == "requester",
            "virtual_hosted_style_request": os.environ.get("AWS_VIRTUAL_HOSTING", "")
            .upper()
            .startswith("T"),
        }

        # obstore reads its own AWS_ENDPOINT_URL; fall back to GDAL's
        # AWS_S3_ENDPOINT (a bare host, not a URL) for parity with the
        # rasterio read path.
        endpoint = os.environ.get("AWS_ENDPOINT_URL") or os.environ.get(
            "AWS_S3_ENDPOINT"
        )
        if endpoint and not endpoint.startswith("http"):
            endpoint = f"https://{endpoint}"
        if endpoint:
            opts["endpoint"] = endpoint  # obstore rejects endpoint=None

        if os.environ.get("AWS_PROFILE"):
            # obstore's native auth does not read ~/.aws/credentials or
            # AWS_PROFILE. The documented route is the boto3 credential
            # provider, hence the optional `boto3` dependency.
            try:
                import boto3
            except ImportError as exc:
                raise ImportError(
                    "AWS_PROFILE is set but `boto3` is not installed. Install "
                    "the `titiler-openeo[boto3]` extra to use profile/SSO "
                    "credentials, or unset AWS_PROFILE to use static keys, "
                    "IRSA or IMDS instead."
                ) from exc
            from obstore.auth.boto3 import Boto3CredentialProvider

            opts["credential_provider"] = Boto3CredentialProvider(
                boto3.Session(profile_name=os.environ["AWS_PROFILE"])
            )
        elif os.environ.get("AWS_ACCESS_KEY_ID"):
            opts["access_key_id"] = os.environ["AWS_ACCESS_KEY_ID"]
            opts["secret_access_key"] = os.environ.get("AWS_SECRET_ACCESS_KEY", "")
            if os.environ.get("AWS_SESSION_TOKEN"):
                opts["token"] = os.environ["AWS_SESSION_TOKEN"]
        else:
            opts["skip_signature"] = True

        return opts


_default_fetcher: Optional[AssetFetcher] = None


def get_default_fetcher() -> AssetFetcher:
    """Return the process-wide default AssetFetcher (lazily constructed)."""
    global _default_fetcher
    if _default_fetcher is None:
        _default_fetcher = ObstoreFetcher()
    return _default_fetcher
