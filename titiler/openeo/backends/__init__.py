"""titiler openeo STAC Backend."""

from ..settings import STACSettings
from .base import BaseBackend  # noqa


def create_backend(settings: STACSettings):
    """Create STAC Backend from STAC URL."""
    if settings.api_url:
        from .stacapi import stacApiBackend  # noqa

        return stacApiBackend(settings.api_url)  # type: ignore

    elif settings.pgstac_url:
        from .pgstac import pgStacBackend  # noqa

        return pgStacBackend(settings.pgstac_url)  # type: ignore

    raise ValueError("No VALID backend URL provided")
