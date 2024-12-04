"""Titiler-openEO API settings."""

from typing import Union

from pydantic import AnyHttpUrl, Field, PostgresDsn, field_validator
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


class PgSTACSettings(BaseSettings):
    """Settings for PgSTAC Client"""

    # see https://www.psycopg.org/psycopg3/docs/api/pool.html#the-connectionpool-class for options
    # The minimum number of connection the pool will hold
    db_min_conn_size: int = 1

    # The maximum number of connections the pool will hold
    db_max_conn_size: int = 10

    # Maximum number of requests that can be queued to the pool
    db_max_queries: int = 50000

    # Maximum time, in seconds, that a connection can stay unused in the pool before being closed, and the pool shrunk.
    db_max_idle: float = 300

    # Number of background worker threads used to maintain the pool state
    db_num_workers: int = 3

    model_config = SettingsConfigDict(
        env_prefix="TITILER_OPENEO_PGSTAC_",
        env_file=".env",
        extra="ignore",
    )
