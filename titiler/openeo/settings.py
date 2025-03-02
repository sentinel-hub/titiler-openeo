"""Titiler-openEO API settings."""

from typing import Any, Dict, Optional, Union

from pydantic import AnyHttpUrl, Field, PostgresDsn, field_validator, model_validator
from pydantic.fields import FieldInfo
from pydantic_settings import BaseSettings, EnvSettingsSource, SettingsConfigDict
from typing_extensions import Annotated


class OIDCConfig(BaseSettings):
    """OIDC configuration settings.
    For now, only supports OpenID Connect (OIDC) Authorization Code Flow with PKCE."""

    client_id: str = ""
    wk_url: str = ""
    redirect_url: str = ""
    scopes: list[str] = ["openid", "email", "profile"]
    name_claim: str = "name"
    title: str = "OIDC"
    description: str = "OpenID Connect (OIDC) Authorization Code Flow with PKCE"

    model_config = SettingsConfigDict(
        env_prefix="TITILER_OPENEO_AUTH_OIDC_",
        env_file=".env",
        extra="ignore",
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls,
        init_settings,
        env_settings,
        dotenv_settings,
        file_secret_settings,
    ):
        """
        Customize sources for settings construction.

        This method allows customization of the settings sources hierarchy by inserting
        a custom settings source after the init_settings source.

        Args:
            settings_cls (Type[BaseSettings]): The settings class being constructed.
            init_settings (SettingsSource): Settings from direct class instantiation.
            env_settings (SettingsSource): Settings from environment variables.
            dotenv_settings (SettingsSource): Settings from .env file.
            file_secret_settings (SettingsSource): Settings from secrets file.

        Returns:
            tuple: A tuple containing the settings sources in order of precedence:
                - init_settings
                - custom settings source
                - dotenv_settings
                - file_secret_settings
        """
        return (
            init_settings,
            cls.CustomSettingsSource(settings_cls),
            dotenv_settings,
            file_secret_settings,
        )

    class CustomSettingsSource(EnvSettingsSource):
        """Custom settings source for handling environment variables.

        Extends EnvSettingsSource to provide custom parsing of environment variables.
        """

        def prepare_field_value(
            self, field_name: str, field: FieldInfo, value: Any, value_is_complex: bool
        ) -> Any:
            """Prepare field value from environment variable.

            Args:
                field_name: Name of the field being processed
                field: Field information
                value: Raw value from environment
                value_is_complex: Whether the value is a complex type

            Returns:
                Processed value for the field
            """
            # allow space-separated list parsing for scopes
            if field_name == "scopes":
                return value.split(" ") if value else None

            return super().prepare_field_value(
                field_name, field, value, value_is_complex
            )


class AuthSettings(BaseSettings):
    """Authentication settings."""

    # Authentication method
    method: str = "basic"

    # Dictionary of users with access
    # Only used if method is set to "basic"
    users: Dict[str, Any] = {
        "test": {
            "password": "test",
            "roles": ["user"],
        }
    }

    # OIDC configuration
    # Only used if method is set to "oidc"
    oidc: Optional[OIDCConfig] = None

    model_config = SettingsConfigDict(
        env_prefix="TITILER_OPENEO_AUTH_",
        env_file=".env",
        extra="ignore",
    )

    def __init__(self, *args, **kwargs):
        """Initialize settings."""
        kwargs["oidc"] = OIDCConfig()
        super().__init__(*args, **kwargs)

    @model_validator(mode="after")
    def validate_oidc_config(self):
        """Validate OIDC configuration when method is oidc."""
        if self.method == "oidc" and not self.oidc:
            raise ValueError("OIDC configuration required when method is 'oidc'")
        return self


class ApiSettings(BaseSettings):
    """FASTAPI application settings."""

    name: str = "TiTiler-OpenEO"
    cors_origins: str = "*"
    cors_allow_methods: str = "GET,POST,DELETE,OPTIONS"
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


class BackendSettings(BaseSettings):
    """OpenEO Backend settings."""

    stac_api_url: Union[AnyHttpUrl, PostgresDsn]
    service_store_url: Union[AnyHttpUrl, str]

    model_config = SettingsConfigDict(
        env_prefix="TITILER_OPENEO_",
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


class ProcessingSettings(BaseSettings):
    """Processing settings"""

    # Maximum allowed pixel count (width * height) for image processing
    max_pixels: int = 100_000_000  # 100 million pixels default
    max_items: int = 20

    model_config = SettingsConfigDict(
        env_prefix="TITILER_OPENEO_PROCESSING_",
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
