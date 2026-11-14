"""Titiler-openEO API settings."""

import json
import logging
from typing import Annotated, Any, Dict, List, Literal, Optional, TypeVar, Union

from pydantic import (
    AnyHttpUrl,
    BeforeValidator,
    Field,
    PostgresDsn,
    ValidationError,
    field_validator,
    model_validator,
)
from pydantic.fields import FieldInfo
from pydantic_settings import (
    BaseSettings,
    NoDecode,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

logger = logging.getLogger(__name__)

#: The grant types (flows) the openEO API allows a default client to declare.
OIDCGrantType = Literal[
    "implicit",
    "authorization_code",
    "authorization_code+pkce",
    "urn:ietf:params:oauth:grant-type:device_code",
    "urn:ietf:params:oauth:grant-type:device_code+pkce",
    "refresh_token",
]

#: openEO API: a provider id matches `[\d\w]{1,20}`, an ECMA-262 pattern, so
#: `\w` is ASCII. Clients send the id back in a header, as the middle part of
#: the `oidc/<provider_id>/<token>` bearer token, so a trailing newline must
#: not pass either: in pydantic's pattern, unlike Python's `re`, `$` is the
#: end of the text. `OIDCConfig` strips surrounding whitespace first.
OIDCProviderId = Annotated[str, Field(pattern=r"^[A-Za-z0-9_]{1,20}$")]


def _split_list(value: Any) -> Any:
    """Read a string as a JSON array or as space-separated values."""
    if isinstance(value, str):
        if value.strip().startswith("["):
            try:
                return json.loads(value)
            except json.JSONDecodeError as err:
                raise ValueError(
                    f"starts with '[' but is not a valid JSON array ({err})"
                ) from err
        return value.split()
    return value


_Item = TypeVar("_Item")

#: A list setting, as space-separated values (one value is a list of one) or
#: a JSON array. `NoDecode` stops pydantic-settings from decoding variables as
#: JSON, which refuses space-separated values; `_split_list` reads both.
SpaceSeparated = Annotated[list[_Item], NoDecode, BeforeValidator(_split_list)]


class _BlankIsUnset(PydanticBaseSettingsSource):
    """Leave out the variables that hold only whitespace.

    `env_ignore_empty` leaves out only `""`: a variable set to `" "` split to
    `[]` and replaced a list default, so `GET /credentials/oidc` advertised no
    `openid` scope. Only the environment and `.env` are wrapped: an explicit
    argument is still validated as given.
    """

    def __init__(self, source: PydanticBaseSettingsSource):
        super().__init__(source.settings_cls)
        self._source = source
        # The name the settings build keys this source's values on.
        self.__name__ = type(source).__name__

    def get_field_value(
        self, field: FieldInfo, field_name: str
    ) -> tuple[Any, str, bool]:
        """Not used: `__call__` reads all the fields from the wrapped source."""
        return None, "", False

    def __call__(self) -> dict[str, Any]:
        """Return the wrapped source's values, without the blank strings."""
        return {
            name: value
            for name, value in self._source().items()
            if not (isinstance(value, str) and not value.strip())
        }


class OIDCConfig(BaseSettings):
    """OIDC configuration settings.
    For now, only supports OpenID Connect (OIDC) Authorization Code Flow with PKCE."""

    client_id: str = ""
    wk_url: str = ""
    # Advertised to openEO clients as `redirect_urls` in `GET /credentials/oidc`.
    redirect_url: SpaceSeparated[str] = []
    scopes: SpaceSeparated[str] = ["openid", "email", "profile"]
    name_claim: str = "name"
    title: str = "OIDC"
    description: str = "OpenID Connect (OIDC) Authorization Code Flow with PKCE"

    # The provider `id` in `GET /credentials/oidc`. openEO clients send it back
    # as the prefix of the bearer token (`oidc/<provider_id>/<token>`). It
    # identifies the provider, not the protocol: CDSE back-ends use "CDSE".
    # The default keeps the value of the versions before it was configurable
    # (#465).
    provider_id: OIDCProviderId = "oidc"

    # The flows advertised for the default client. Advertise only the flows
    # that the client registration at the provider really allows. The openEO
    # Python client's `authenticate_oidc()` uses the default client only when
    # it lists `refresh_token` and a device code flow, and then fails if the
    # provider has the device code flow disabled for the client (#465).
    grant_types: Annotated[SpaceSeparated[OIDCGrantType], Field(min_length=1)] = [
        "authorization_code+pkce",
        "urn:ietf:params:oauth:grant-type:device_code+pkce",
        "refresh_token",
    ]

    # Additional accepted `aud` values, space-separated, beyond `client_id`.
    # Needed for providers that issue *access* tokens audienced at an API
    # rather than at the client -- Microsoft Entra uses the application ID URI
    # (`api://<client_id>`). Empty by default, which accepts exactly what this
    # backend accepted before the setting existed.
    # See docs/adr/0006-microsoft-entra-oidc.md S2.2 change 6.
    audiences: SpaceSeparated[str] = []

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
    allowed_tenants: SpaceSeparated[str] = []

    @field_validator("grant_types")
    @classmethod
    def _check_grant_types(cls, value: list[str]) -> list[str]:
        """Refuse a list that the openEO API forbids or no client can use."""
        if len(set(value)) != len(value):
            raise ValueError("lists a grant type twice")
        if set(value) == {"refresh_token"}:
            raise ValueError(
                "lists only refresh_token, which cannot get a client its first token"
            )
        return value

    model_config = SettingsConfigDict(
        env_prefix="TITILER_OPENEO_AUTH_OIDC_",
        env_file=".env",
        # A Helm `|` block or a Secret built from a file ends with a newline.
        # Kept, it fails every login: `client_id` would not match the token
        # `aud`. This also strips each item of the list settings.
        str_strip_whitespace=True,
        extra="ignore",
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """A blank variable keeps the default, as in the Helm chart."""
        return (
            init_settings,
            _BlankIsUnset(env_settings),
            _BlankIsUnset(dotenv_settings),
            file_secret_settings,
        )


class AuthSettings(BaseSettings):
    """Authentication settings."""

    # Authentication method
    method: str = "basic"

    # Dictionary of users with access, keyed by username
    # Required if method is set to "basic": there is no built-in user
    users: Dict[str, Any] = {}

    # OIDC configuration. Read from the environment only if method is "oidc".
    oidc: Optional[OIDCConfig] = None

    model_config = SettingsConfigDict(
        env_prefix="TITILER_OPENEO_AUTH_",
        env_file=".env",
        extra="ignore",
        # Start-up errors are logged; never echo the users (and passwords) in them.
        hide_input_in_errors=True,
    )

    @model_validator(mode="after")
    def validate_oidc_config(self):
        """Fail at startup, not at the first request, on incomplete OIDC config.

        The OIDC settings are read only for the "oidc" method, so leftover
        OIDC variables cannot stop another method from starting. An `oidc`
        argument is kept (docs/adr/0006-microsoft-entra-oidc.md S1.2 gap 8).
        """
        if self.method != "oidc":
            return self

        if self.oidc is None:
            try:
                self.oidc = OIDCConfig()
            except ValidationError as err:
                # Raised as is, its errors would be reported for `AuthSettings`,
                # by field name only: say which variable (and list item) is
                # wrong, as the hidden input cannot show it.
                reasons = "; ".join(
                    f"TITILER_OPENEO_AUTH_OIDC_{str(error['loc'][0]).upper()}"
                    + (f" item {error['loc'][1] + 1}" if len(error["loc"]) > 1 else "")
                    + f": {error['msg']}"
                    for error in err.errors()
                )
                raise ValueError(f"Invalid OIDC settings. {reasons}") from err

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

        if not self.oidc.redirect_url and any(
            grant.startswith(("implicit", "authorization_code"))
            for grant in self.oidc.grant_types
        ):
            # openEO API: redirect URLs MUST be given for these flows. A
            # warning, not an error: device code logins work without them.
            logger.warning(
                "The OIDC default client lists an authorization code or implicit "
                "flow, but TITILER_OPENEO_AUTH_OIDC_REDIRECT_URL is empty: the "
                "openEO Web Editor will ask users for a client ID."
            )
        return self

    @model_validator(mode="after")
    def validate_basic_users(self):
        """Refuse to start basic auth without usable users, rather than fall back."""
        if self.method == "basic" and not (
            self.users
            and all(
                # BasicAuth splits credentials on the first ":", so a username
                # that is empty or contains one could never log in.
                name
                and ":" not in name
                and isinstance(user, dict)
                and isinstance(user.get("password"), str)
                and user["password"]
                for name, user in self.users.items()
            )
        ):
            raise ValueError(
                "Basic authentication is enabled but TITILER_OPENEO_AUTH_USERS is "
                "empty or has an invalid user: each needs a non-empty username "
                'without ":" and a non-empty "password". Set it to a JSON object '
                "keyed by username, "
                'e.g. \'{"alice": {"password": "..."}}\', '
                "or set TITILER_OPENEO_AUTH_METHOD=oidc."
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


#: The scale/offset sources a deployment can list. ``"stac"`` and ``"cog"``
#: are built in; every other name is a plugin in
#: `titiler.openeo.scaleoffset.PLUGINS` (a test keeps the two in sync). A
#: `Literal` rather than a check against the registry, so that this module
#: does not import the plugins (which import these settings).
ScaleOffsetSource = Literal["stac", "cog", "sentinel2-boa"]

DEFAULT_SCALE_OFFSET_SOURCES: List[ScaleOffsetSource] = [
    "stac",
    "sentinel2-boa",
    "cog",
]


def _parse_scale_offset_sources(value: Any) -> Any:
    """Accept a JSON list or a comma-separated string, and reject duplicates."""
    if isinstance(value, str):
        text = value.strip()
        if text.startswith("["):
            import json

            value = json.loads(text)
        else:
            value = [v.strip() for v in text.split(",") if v.strip()]
    if isinstance(value, list) and len(set(value)) != len(value):
        raise ValueError(f"duplicate scale/offset source in {value!r}")
    return value


class ProcessingSettings(BaseSettings):
    """Processing settings"""

    # Maximum allowed pixel count (width * height) for image processing
    max_pixels: int = 100_000_000  # 100 million pixels default
    max_items: int = 20

    # Maximum number of geometries that a process can make or load in a vector
    # data cube. A noisy raster can give one polygon for each pixel in
    # raster_to_vector (ADR 0014 §2.7).
    max_vector_features: int = 100_000

    # Maximum number of pixel edges between zones that raster_to_vector
    # accepts, for all dates. GDAL makes all the polygons before it gives the
    # first one, so max_vector_features does not limit the work. The cost
    # follows these edges, not the pixels: about 700 ns and 120 bytes for each
    # edge (5 million: about 4 s and 600 MB).
    max_vectorize_edges: int = 5_000_000

    # Maximum size in bytes of a vector file that load_url downloads in full
    # (GeoJSON, GeoParquet). GPKG, FlatGeobuf and Shapefile are read with
    # range requests and are limited by max_vector_features only.
    max_vector_download_bytes: int = 256 * 1024 * 1024

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

    # Scale/offset sources, in order. For each band the first source that has
    # a scale/offset (not 1/0) wins, so a band is scaled one time only. A
    # source that does not apply to an item (e.g. no product XML) is skipped.
    # JSON list or comma list, e.g. "stac,sentinel2-boa,cog". Empty = none.
    scale_offset_sources: Annotated[List[ScaleOffsetSource], NoDecode] = Field(
        default_factory=lambda: list(DEFAULT_SCALE_OFFSET_SOURCES)
    )

    # Per-collection source lists, as JSON: collection id -> ordered list. A
    # list given here replaces `scale_offset_sources` for that collection; an
    # empty list turns scale/offset off for it.
    # e.g. '{"sentinel-2-l2a": ["sentinel2-boa"], "raw-dn": []}'
    scale_offset_collections: Dict[str, List[ScaleOffsetSource]] = Field(
        default_factory=dict
    )

    @field_validator("scale_offset_sources", mode="before")
    @classmethod
    def _parse_sources(cls, value: Any) -> Any:
        return _parse_scale_offset_sources(value)

    @field_validator("scale_offset_collections", mode="before")
    @classmethod
    def _parse_collections(cls, value: Any) -> Any:
        if isinstance(value, dict):
            return {k: _parse_scale_offset_sources(v) for k, v in value.items()}
        return value

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

    # Max number of parsed MTD_MSIL2A.xml product-metadata objects (BOA
    # quantification/offset, for the "sentinel2-boa" scale/offset source) to
    # keep cached -- one entry per product.
    product_metadata_cache_maxsize: int = 128

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
        True,
        description=(
            "Test a connection before use; drops stale ones, so the first "
            "request after a database restart reconnects instead of failing."
        ),
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
