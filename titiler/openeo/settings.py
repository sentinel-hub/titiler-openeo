"""Titiler-openEO API settings."""

from typing import Annotated, Any, Dict, Optional, Union

from pydantic import (
    AnyHttpUrl,
    BaseModel,
    ConfigDict,
    Field,
    PostgresDsn,
    field_validator,
    model_validator,
)
from pydantic.fields import FieldInfo
from pydantic_settings import (
    BaseSettings,
    EnvSettingsSource,
    NoDecode,
    SettingsConfigDict,
)


class OIDCConfig(BaseSettings):
    """OIDC configuration settings.
    For now, only supports OpenID Connect (OIDC) Authorization Code Flow with PKCE."""

    client_id: str = ""
    wk_url: str = ""
    # Space-separated in the environment; a single URL stays valid. Advertised
    # to openEO clients as `redirect_urls` in `GET /credentials/oidc`.
    redirect_url: list[str] = []
    scopes: list[str] = ["openid", "email", "profile"]
    name_claim: str = "name"
    title: str = "OIDC"
    description: str = "OpenID Connect (OIDC) Authorization Code Flow with PKCE"

    # Additional accepted `aud` values, space-separated, beyond `client_id`.
    # Needed for providers that issue *access* tokens audienced at an API
    # rather than at the client -- Microsoft Entra uses the application ID URI
    # (`api://<client_id>`). Empty by default, which accepts exactly what this
    # backend accepted before the setting existed.
    # See docs/adr/0006-microsoft-entra-oidc.md S2.2 change 6.
    audiences: list[str] = []

    # Which token claim becomes `User.user_id`. Services, UDPs and tile
    # assignments are all keyed on it, so changing this on a running deployment
    # orphans everything already stored. `sub` is the specification's answer and
    # the default; Entra's `sub` is pairwise (stable per user *per application
    # registration*), so a deployment that expects to re-register its app may
    # prefer the tenant-stable `oid`. See ADR 0006 S2.4.
    user_id_claim: str = "sub"

    # Entra tenant ids (`tid` claim) allowed to sign in, space-separated. Empty
    # accepts every tenant the issuer accepts -- with a multi-tenant (`common`)
    # discovery URL, that is every Microsoft work and personal account. The
    # personal-account (MSA) tenant is 9188040d-6c67-4c5b-b112-36a304b66dad.
    # When set, a token without `tid` is rejected. See ADR 0006 S2.3.
    allowed_tenants: list[str] = []

    @field_validator("redirect_url", mode="before")
    @classmethod
    def _split_redirect_url(cls, value: Any) -> Any:
        """Accept one URL or a space-separated list, as a `str` or a list."""
        if isinstance(value, str):
            return value.split()
        return value

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
            # allow space-separated list parsing for list-valued fields
            if field_name in (
                "scopes",
                "audiences",
                "redirect_url",
                "allowed_tenants",
            ):
                return value.split() if value else None

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
        """Initialize settings, defaulting `oidc` from the environment.

        `setdefault`, not an unconditional assignment: overwriting `kwargs`
        made `AuthSettings(oidc=...)` silently ignored, which in turn made
        `validate_oidc_config` below and the `if not settings.oidc` guards in
        `auth.py` dead code (docs/adr/0006-microsoft-entra-oidc.md S1.2 gap 8).
        """
        kwargs.setdefault("oidc", OIDCConfig())
        super().__init__(*args, **kwargs)

    @model_validator(mode="after")
    def validate_oidc_config(self):
        """Fail at startup, not at the first request, on incomplete OIDC config."""
        if self.method != "oidc":
            return self

        if not self.oidc:
            raise ValueError("OIDC configuration required when method is 'oidc'")

        missing = [
            f"TITILER_OPENEO_AUTH_OIDC_{name.upper()}"
            for name in ("client_id", "wk_url")
            if not getattr(self.oidc, name)
        ]
        if missing:
            raise ValueError(
                "OIDC authentication is enabled but incompletely configured. "
                f"Missing: {', '.join(missing)}."
            )
        return self


class ApiSettings(BaseSettings):
    """FASTAPI application settings."""

    name: str = "TiTiler-OpenEO"
    cors_origins: str = "*"
    cors_allow_methods: str = "GET,POST,PUT,PATCH,DELETE,OPTIONS"
    # Cache settings for different endpoint types
    cache_static: str = "public, max-age=3600"  # For static resources
    cache_tiles: str = (
        "public, max-age=3600"  # For XYZ tile endpoints (browser cacheable)
    )
    # For XYZ tiles of private or restricted services (never in shared caches)
    cache_tiles_private: str = "private, max-age=3600"
    # For XYZ tile errors that are the same for every caller (zoom out of
    # range, no data) on public services. Ignored when `cache_tiles` has
    # `no-store` (see `tile_errors_policy`).
    cache_tile_errors: str = "private, max-age=60"
    cache_dynamic: str = "no-cache"  # For dynamic endpoints that need fresh data
    cache_default: str = "no-store"  # Default policy for other endpoints
    root_path: str = ""

    debug: bool = False

    model_config = SettingsConfigDict(
        env_prefix="TITILER_OPENEO_API_", env_file=".env", extra="ignore"
    )

    @property
    def tile_errors_policy(self) -> str:
        """Cache-Control for tile errors that are the same for every caller."""
        if "no-store" in self.cache_tiles.lower():
            return "no-store"
        return self.cache_tile_errors

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
    store_url: Union[AnyHttpUrl, str]
    tile_store_url: Optional[str] = None  # URL for tile assignment store
    default_services_file: Optional[str] = (
        None  # Path to default services configuration file
    )
    # `NoDecode` so `parse_exclude_collections` below actually gets a chance to
    # run. Without it pydantic-settings treats a `list[str]` as complex and
    # JSON-decodes the environment value first, so the documented
    # comma-separated form raised `SettingsError` instead of parsing -- i.e.
    # this setting could not be set from the environment at all.
    exclude_collections: Annotated[list[str], NoDecode] = Field(
        default_factory=list,
        description="List of collection IDs to exclude from the API (e.g. non-compliant STAC collections).",
    )

    model_config = SettingsConfigDict(
        env_prefix="TITILER_OPENEO_",
        env_file=".env",
        extra="ignore",
    )

    @field_validator("exclude_collections", mode="before")
    @classmethod
    def parse_exclude_collections(cls, v):
        """Parse comma-separated string into list."""
        if isinstance(v, str):
            return [c.strip() for c in v.split(",") if c.strip()]
        return v


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


class ScaleOffsetSources(BaseModel):
    """Per-collection override of the scale/offset sources.

    A source left unset (``None``) uses the global default
    (`ProcessingSettings.scale_offset_stac` / `scale_offset_cog`). Unknown keys
    are rejected, so a typing error fails at startup instead of being ignored.
    """

    model_config = ConfigDict(extra="forbid")

    stac: Optional[bool] = None
    cog: Optional[bool] = None


class ProcessingSettings(BaseSettings):
    """Processing settings"""

    # Maximum allowed pixel count (width * height) for image processing
    max_pixels: int = 100_000_000  # 100 million pixels default
    max_items: int = 20

    # Free a process-graph node's intermediate data as soon as every consumer of
    # that node has run (reference-counted results cache), instead of keeping
    # every intermediate cube resident for the whole evaluation. Bounds peak
    # memory to ~the working set. See titiler.openeo.results_cache.
    evict_intermediate_results: bool = True

    # Apply scale/offset (per band) when loading so bands are returned as
    # physical values (e.g. Sentinel-2 BOA reflectance) instead of raw DN.
    # Master switch: disable to keep raw DN everywhere (e.g. while migrating
    # graphs that scale manually), whatever the per-source settings say.
    apply_scale_offset: bool = True

    # Global defaults for the two scale/offset sources. For each band the STAC
    # `raster:scale`/`raster:offset` wins; the COG header scale/offset is used
    # only when STAC has none. A band is scaled one time only.
    scale_offset_stac: bool = True
    scale_offset_cog: bool = True

    # Per-collection overrides, as JSON: collection id -> {"stac": bool,
    # "cog": bool}. A source that is not given uses the global default above.
    # e.g. '{"sentinel-2-l2a": {"stac": false}, "raw-dn": {"stac": false, "cog": false}}'
    scale_offset_collections: Dict[str, ScaleOffsetSources] = Field(
        default_factory=dict
    )

    model_config = SettingsConfigDict(
        env_prefix="TITILER_OPENEO_PROCESSING_",
        env_file=".env",
        extra="ignore",
    )


class SARSettings(BaseSettings):
    """Sentinel-1 SAR backscatter settings.

    See docs/adr/0001-sar-backscatter.md for the design this configures.
    """

    # Max number of parsed calibration/noise LUT sets to keep cached. Each
    # parsed set is ~100 KB; the raw annotation XML they come from is
    # ~1-1.5 MB per polarisation (ADR S7.5), so caching the parsed result
    # rather than the bytes is what keeps this cheap.
    annotation_cache_maxsize: int = 128

    model_config = SettingsConfigDict(
        env_prefix="TITILER_OPENEO_SAR_",
        env_file=".env",
        extra="ignore",
    )


class Sentinel2Settings(BaseSettings):
    """Sentinel-2 view/sun angle band settings.

    See docs/adr/0004-sentinel2-view-sun-angle-bands.md for the design this
    configures.
    """

    # Max number of parsed MTD_TL.xml tile-metadata objects to keep cached,
    # mirroring SARSettings.annotation_cache_maxsize -- one entry per granule.
    tile_metadata_cache_maxsize: int = 128

    model_config = SettingsConfigDict(
        env_prefix="TITILER_OPENEO_SENTINEL2_",
        env_file=".env",
        extra="ignore",
    )


class StoreSettings(BaseSettings):
    """Connection pool settings for the SQLAlchemy stores.

    The services, UDP and tile stores share one engine, and so one pool, per
    database URL and worker process. Each process can open up to
    `pool_size + max_overflow` connections. See "Store Settings" in the admin
    guide for how to size this against the database's `max_connections`.

    The pool settings do not apply to SQLite.
    """

    pool_size: int = Field(5, ge=1, description="Connections kept open per engine.")
    max_overflow: int = Field(
        10, ge=0, description="Extra connections an engine may open under load."
    )
    pool_timeout: float = Field(
        5,
        gt=0,
        description=(
            "Seconds to wait for a free connection before failing. Keep it short: "
            "a waiting request holds a worker thread."
        ),
    )
    pool_recycle: int = Field(
        -1,
        description=(
            "Seconds after which a connection is replaced. Set it below the idle "
            "timeout of any proxy or pooler in front of the database. -1 disables."
        ),
    )
    pool_pre_ping: bool = Field(
        False, description="Test a connection before use; drops stale ones."
    )

    model_config = SettingsConfigDict(
        env_prefix="TITILER_OPENEO_STORE_",
        env_file=".env",
        extra="ignore",
        # An empty variable (e.g. a Helm value set to null) keeps the default
        # instead of failing the import-time store setup.
        env_ignore_empty=True,
    )

    @field_validator("pool_recycle")
    @classmethod
    def _recycle_disabled_is_minus_one(cls, value: int) -> int:
        # SQLAlchemy reads 0 as "replace on every checkout", not "disabled".
        if value == 0 or value < -1:
            raise ValueError("pool_recycle must be -1 (disabled) or > 0 seconds")
        return value


class SigningSettings(BaseSettings):
    """Which signer this deployment's assets need.

    Deliberately a setting rather than something the application infers. An
    earlier version turned signing on when the configured STAC API URL happened
    to be `planetarycomputer.microsoft.com`, which put a cloud provider's
    hostname in the application's decision logic (issue #377). A deployment
    knows its own data provider; the application should not guess.

    Empty -- the default -- means no signing, which is the behaviour every
    deployment had before signing existed. Valid values are the keys of
    `signing.SIGNERS`; an unknown one fails loudly at first use rather than
    reading as "signing off" (see `signing.get_signer`).

    See docs/adr/0005-asset-href-signing.md for the design this configures.
    """

    asset_signer: str = ""

    model_config = SettingsConfigDict(
        env_prefix="TITILER_OPENEO_",
        env_file=".env",
        extra="ignore",
    )


class PlanetaryComputerSettings(BaseSettings):
    """Microsoft Planetary Computer asset-signing settings.

    Only consulted when `SigningSettings.asset_signer` names
    `"planetary-computer"`.

    See docs/adr/0005-asset-href-signing.md for the design this configures.
    """

    # Base URL of the Planetary Computer Data Authentication API. Only the
    # `/token/{account}/{container}` shape is used -- `/sign?href=` costs one
    # round-trip per asset, which a mosaic read cannot afford (ADR 0005 S2.4).
    sas_url: str = "https://planetarycomputer.microsoft.com/api/sas/v1"

    # Optional subscription key, sent as `Ocp-Apim-Subscription-Key`. It has no
    # observable effect on the minted token's scope or duration (ADR 0005 S1.2)
    # but is the documented rate-limit lever.
    subscription_key: str = ""

    # How long before a token's stated expiry to mint a replacement. Tokens
    # last ~45 minutes, so this trades a little freshness for never handing a
    # nearly-dead token to a read that is about to start.
    expiry_margin: Annotated[float, Field(ge=0.0)] = 300.0

    # Per-request timeout, in seconds, for minting a token.
    timeout: Annotated[float, Field(gt=0.0)] = 10.0

    model_config = SettingsConfigDict(
        env_prefix="TITILER_OPENEO_PC_",
        env_file=".env",
        extra="ignore",
    )


class HealthSettings(BaseSettings):
    """Settings for the /healthz and /readyz health endpoints."""

    # Per-check timeout in seconds for the /readyz probe
    check_timeout: Annotated[float, Field(gt=0.0)] = 2.0

    model_config = SettingsConfigDict(
        env_prefix="TITILER_OPENEO_HEALTH_",
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
