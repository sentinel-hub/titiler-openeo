"""Titiler-openEO API settings."""

from typing import Union

from pydantic import AnyHttpUrl, Field, PostgresDsn, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing_extensions import Annotated


class ApiSettings(BaseSettings):
    """FASTAPI application settings."""

    name: str = "TiTiler-OpenEO"
    cors_origins: str = "*"
    cors_allow_methods: str = "GET,OPTIONS"
    cachecontrol: str = "public, max-age=3600"
    root_path: str = ""

    debug: bool = False

    model_config = SettingsConfigDict(
        env_prefix="TITILER_OPENEO_API_", env_file=".env", extra="ignore"
    )

    @field_validator("cors_origins")
    def parse_cors_origin(cls, v):
        """Parse CORS origins."""
        return [origin.strip() for origin in v.split(",")]

    @field_validator("cors_allow_methods")
    def parse_cors_allow_methods(cls, v):
        """Parse CORS allowed methods."""
        return [method.strip().upper() for method in v.split(",")]


class STACSettings(BaseSettings):
    """STAC settings."""

    api_url: Union[AnyHttpUrl, PostgresDsn]

    model_config = SettingsConfigDict(
        env_prefix="TITILER_OPENEO_STAC_",
        env_file=".env",
        extra="ignore",
    )


class PySTACSettings(BaseSettings):
    """Settings for PySTAC Client"""

    # Total number of retries to allow.
    retry: Annotated[int, Field(ge=0)] = 3

    # A backoff factor to apply between attempts after the second try
    retry_factor: Annotated[float, Field(ge=0.0)] = 0.0

    model_config = SettingsConfigDict(
        env_prefix="TITILER_OPENEO_PYSTAC_",
        env_file=".env",
        extra="ignore",
    )


class CacheSettings(BaseSettings):
    """Cache settings"""

    # TTL of the cache in seconds
    ttl: int = 300

    # Maximum size of the cache in Number of element
    maxsize: int = 512

    # Whether or not caching is enabled
    disable: bool = False

    model_config = SettingsConfigDict(
        env_prefix="TITILER_OPENEO_CACHE_",
        env_file=".env",
        extra="ignore",
    )

    @model_validator(mode="after")
    def check_enable(self):
        """Check if cache is disabled."""
        if self.disable:
            self.ttl = 0
            self.maxsize = 0

        return self
