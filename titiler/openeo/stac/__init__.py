"""titiler openeo STAC Backend."""

from urllib.parse import urlparse

from .base import STACBackend  # noqa


def get_stac_backend(url: str, **kwargs):
    """Return STAC Backend from STAC URL."""
    parsed = urlparse(url)
    if parsed.scheme in ["https", "http"]:
        from .stacapi import stacApiBackend  # noqa

        return stacApiBackend(url, **kwargs)  # type: ignore

    elif parsed.scheme.startswith("postgres"):
        from .pgstac import pgStacBackend  # noqa

        return pgStacBackend(url, **kwargs)  # type: ignore

    else:
        raise ValueError(f"Unsupported STAC backend: {url}")
