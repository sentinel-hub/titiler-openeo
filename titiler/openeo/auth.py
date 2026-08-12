"""titiler.openeo.auth."""

import abc
import base64
import json
import logging
import time
from base64 import b64decode
from enum import Enum
from threading import Lock
from typing import Any, Dict, Literal, Optional

from attrs import define, field
from fastapi import Header
from fastapi.exceptions import HTTPException
from fastapi.security.utils import get_authorization_scheme_param
from pydantic import BaseModel, Field, field_validator
from starlette.status import HTTP_401_UNAUTHORIZED, HTTP_403_FORBIDDEN
from typing_extensions import Self

from .models.auth import BasicAuthUser, User
from .services.base import ServicesStore
from .settings import AuthSettings, OIDCConfig

try:
    import httpx
except ImportError:  # pragma: nocover
    httpx = None  # type: ignore

try:
    import cryptography
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding
    from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicNumbers
except ImportError:  # pragma: nocover
    httpx = None  # type: ignore
    cryptography = None  # type: ignore
    InvalidSignature = None  # type: ignore
    hashes = None  # type: ignore
    padding = None  # type: ignore
    RSAPublicNumbers = None  # type: ignore


logger = logging.getLogger(__name__)

#: The only signature algorithm this backend verifies. Pinned from the token
#: header rather than trusted from it: verification below is hardcoded to
#: RS256 (PKCS1v15 + SHA256), so accepting a token that *claims* anything else
#: would be an algorithm-confusion hole. Microsoft Entra advertises exactly
#: this one (`id_token_signing_alg_values_supported: ["RS256"]`), as does the
#: Keycloak realm already in production. Supporting a second algorithm is the
#: documented trigger to replace this module with a JWT library
#: (docs/adr/0006-microsoft-entra-oidc.md S2.1).
_SUPPORTED_ALG = "RS256"

#: Clock-skew allowance, in seconds, for `exp` and `nbf`.
_CLOCK_SKEW_LEEWAY = 60.0

#: How long a provider's discovery document is trusted before it is refetched.
_DISCOVERY_TTL = 3600.0

#: Minimum gap between JWKS refetches triggered by an unknown `kid`. Rotation
#: is continuous but slow; without this floor, a flood of tokens carrying junk
#: kids would become a flood of requests to the provider (ADR 0006 S3.1).
_JWKS_REFRESH_COOLDOWN = 300.0

#: Issuer placeholder returned by Entra's multi-tenant `common` endpoint.
#: Useless for comparison and invalid to advertise, so it is rejected with a
#: message naming the fix rather than silently accepted (ADR 0006 S2.3).
_TEMPLATED_ISSUER_MARKER = "{tenantid}"


class AuthMethod(Enum):
    """Authentication Method."""

    basic = "basic"
    oidc = "oidc"


@define(kw_only=True)
class Auth(metaclass=abc.ABCMeta):
    """Auth BaseClass."""

    method: AuthMethod = field(init=False)
    store: ServicesStore = field()

    @abc.abstractmethod
    def login(self, authorization: str = Header()) -> Any:
        """Validate login and/or create a new user."""
        ...

    @abc.abstractmethod
    def validate(self, authorization: str = Header()) -> User:
        """Validate Bearer Token."""
        ...

    def validate_optional(
        self, authorization: str = Header(default=None)
    ) -> Optional[User]:
        """Validate Bearer Token but allow unauthenticated access."""
        if not authorization:
            return None
        return self.validate(authorization)


def get_auth(settings: AuthSettings, store: ServicesStore) -> "Auth":
    """Get Auth instance."""
    if settings.method == AuthMethod.basic.value:
        return BasicAuth(settings=settings, store=store)
    elif settings.method == AuthMethod.oidc.value:
        if not settings.oidc:
            raise ValueError("OIDC configuration required")
        return OIDCAuth(settings=settings, store=store)
    else:
        raise NotImplementedError(f"Auth method {settings.method} not implemented")


@define(kw_only=True)
class OIDCAuth(Auth):
    """OpenID Connect authentication implementation."""

    method: AuthMethod = field(default=AuthMethod("oidc"), init=False)
    # `factory`, not `default`: a bare `AuthSettings()` here is evaluated at
    # class-definition time, i.e. at import, which reads the environment before
    # any caller can configure it and would now also run this class's own
    # startup validation at the wrong moment.
    settings: AuthSettings = field(factory=AuthSettings)
    _config_cache: Optional[Dict] = field(default=None, init=False)
    _config_fetched_at: float = field(default=0.0, init=False)
    _jwks_cache: Optional[Dict] = field(default=None, init=False)
    # When the JWKS was last refetched *because a `kid` was unknown*. Tracked
    # separately from the initial fetch: the cooldown exists to damp a storm of
    # junk kids, and must not block the first genuine rotation, which typically
    # arrives moments after the cache was first filled. `None` means "never
    # refreshed", so the first refresh always proceeds.
    _jwks_refreshed_at: Optional[float] = field(default=None, init=False)
    # Two locks, never nested: `get_jwks` needs `jwks_uri` from the discovery
    # document, so a single lock covering both would deadlock on itself
    # (`threading.Lock` is not reentrant). `config` is always resolved before
    # `_jwks_lock` is acquired.
    _config_lock: Lock = field(factory=Lock, init=False)
    _jwks_lock: Lock = field(factory=Lock, init=False)
    _oidc_config: OIDCConfig = field(init=False)

    def __attrs_post_init__(self):
        """Validate OIDC configuration on initialization."""
        assert httpx, "`httpx` module must be installed to use OIDC Auth method"
        assert (
            cryptography
        ), "`cryptography` module must be installed to use OIDC Auth method"

        if not self.settings.oidc:
            raise ValueError("OIDC configuration required")

        self._oidc_config = self.settings.oidc

    @property
    def config(self) -> Dict:
        """The provider's discovery document, cached for `_DISCOVERY_TTL`.

        Previously cached for the life of the process, which left a deployment
        permanently broken if a provider moved its `jwks_uri`.
        """
        with self._config_lock:
            cached = self._config_cache
            age = time.monotonic() - self._config_fetched_at
            if cached is not None and age <= _DISCOVERY_TTL:
                return cached

            with httpx.Client() as client:
                response = client.get(str(self._oidc_config.wk_url))
                response.raise_for_status()
                config: Dict = response.json()

            self._check_discovery(config)
            self._config_cache = config
            self._config_fetched_at = time.monotonic()
            return config

    @staticmethod
    def _check_discovery(config: Dict) -> None:
        """Reject a discovery document this backend cannot validate against."""
        issuer = config.get("issuer") or ""
        if _TEMPLATED_ISSUER_MARKER in issuer:
            raise ValueError(
                f"The OIDC discovery document advertises a templated issuer "
                f"({issuer!r}), which cannot be validated against a token's `iss` "
                "and cannot be advertised to openEO clients. This is what "
                "Microsoft Entra's multi-tenant `common` endpoint returns; "
                "configure a single-tenant discovery URL instead, e.g. "
                "https://login.microsoftonline.com/<tenant_id>/v2.0/"
                ".well-known/openid-configuration"
            )

    def ping(self, timeout: float = 2.0) -> None:
        """Verify the OIDC well-known endpoint is reachable. Raises on failure."""
        assert httpx, "`httpx` module must be installed to use OIDC Auth method"
        with httpx.Client(timeout=timeout) as client:
            response = client.get(str(self._oidc_config.wk_url))
            response.raise_for_status()

    def get_jwks(self, refresh: bool = False) -> Dict:
        """Get the JSON Web Key Set, optionally forcing a refetch.

        Args:
            refresh: Refetch even if a set is already cached. Rate-limited to
                one refetch per `_JWKS_REFRESH_COOLDOWN`; inside the cooldown
                the cached set is returned unchanged.
        """
        # Resolved before the lock is taken: `config` takes `_config_lock`, and
        # nesting the two would deadlock on a non-reentrant lock.
        jwks_uri = self.config["jwks_uri"]

        with self._jwks_lock:
            cached = self._jwks_cache
            if cached is not None:
                if not refresh:
                    return cached
                if (
                    self._jwks_refreshed_at is not None
                    and time.monotonic() - self._jwks_refreshed_at
                    < _JWKS_REFRESH_COOLDOWN
                ):
                    return cached

            with httpx.Client() as client:
                response = client.get(jwks_uri)
                response.raise_for_status()
                jwks: Dict = response.json()

            self._jwks_cache = jwks
            if refresh:
                self._jwks_refreshed_at = time.monotonic()

            return jwks

    @staticmethod
    def _find_key(jwks: Dict, kid: str):
        """Build the public key for `kid` from `jwks`, or ``None`` if absent."""
        for jwk in jwks.get("keys", []):
            if jwk.get("kid") != kid:
                continue

            if jwk.get("kty") != "RSA":
                raise ValueError(f"Unsupported key type: {jwk.get('kty')}")

            # Convert JWK to public key
            numbers = RSAPublicNumbers(
                e=int.from_bytes(
                    base64.urlsafe_b64decode(jwk["e"] + "=" * (-len(jwk["e"]) % 4)),
                    byteorder="big",
                ),
                n=int.from_bytes(
                    base64.urlsafe_b64decode(jwk["n"] + "=" * (-len(jwk["n"]) % 4)),
                    byteorder="big",
                ),
            )
            return numbers.public_key()

        return None

    def _get_key(self, kid: str):
        """Get the public key for `kid`, refetching the JWKS if it is unknown.

        A `kid` that is not in the cached set is the *expected* steady state,
        not an attack: providers rotate signing keys continuously -- Microsoft
        Entra publishes six at a time. Without this refetch the cache goes
        stale and every login fails until the process restarts
        (docs/adr/0006-microsoft-entra-oidc.md S1.2 gap 1).
        """
        key = self._find_key(self.get_jwks(), kid)
        if key is None:
            logger.info("OIDC key id %r not in cached JWKS; refetching", kid)
            key = self._find_key(self.get_jwks(refresh=True), kid)

        if key is None:
            raise HTTPException(
                status_code=HTTP_401_UNAUTHORIZED,
                detail="Unable to find appropriate key",
            )

        return key

    def _verify_token(self, token: str, key) -> Dict:
        """Verify JWT token signature and claims, and return the payload."""
        try:
            # Split the JWT
            header_b64, payload_b64, signature_b64 = token.split(".")

            # Decode header and payload
            header = json.loads(
                base64.urlsafe_b64decode(header_b64 + "=" * (-len(header_b64) % 4))
            )
            payload = json.loads(
                base64.urlsafe_b64decode(payload_b64 + "=" * (-len(payload_b64) % 4))
            )

            # The signature check below is hardcoded to RS256, so the header's
            # own claim must agree with it rather than be ignored.
            if header.get("alg") != _SUPPORTED_ALG:
                raise ValueError(
                    f"Unsupported token algorithm {header.get('alg')!r}; "
                    f"only {_SUPPORTED_ALG} is accepted"
                )

            # Verify signature
            signature = base64.urlsafe_b64decode(
                signature_b64 + "=" * (-len(signature_b64) % 4)
            )
            key.verify(
                signature,
                f"{header_b64}.{payload_b64}".encode(),
                padding.PKCS1v15(),
                hashes.SHA256(),
            )

            self._verify_claims(payload)
            return payload

        except InvalidSignature as err:
            # `str(InvalidSignature())` is the empty string, so this needs its
            # own message or the failure surfaces as a bare "401:". Report what
            # the token actually says rather than guessing at the cause: the
            # key id was found (or `_get_key` would have failed first), so the
            # question is only ever *why* a known key does not verify it.
            detail = self._explain_bad_signature(header, payload)
            logger.warning("OIDC signature verification failed. %s", detail)
            raise HTTPException(
                status_code=HTTP_401_UNAUTHORIZED,
                detail=detail,
            ) from err
        except ValueError as err:
            raise HTTPException(
                status_code=HTTP_401_UNAUTHORIZED,
                detail=str(err),
            ) from err

    @staticmethod
    def _explain_bad_signature(header: Dict, payload: Dict) -> str:
        """Describe a signature failure using the token's own metadata.

        Nothing secret is echoed -- no signature, no token, only the routing
        claims the caller already holds. `aud` is what decides which key really
        signed the token, so it is the single most useful fact to report.
        """
        aud = payload.get("aud")
        facts = (
            f"alg={header.get('alg')!r} kid={header.get('kid')!r} "
            f"nonce_in_header={'nonce' in header} aud={aud!r} "
            f"iss={payload.get('iss')!r} appid={payload.get('appid') or payload.get('azp')!r}"
        )

        if "nonce" in header:
            # Microsoft's documented behaviour: for tokens audienced at one of
            # its own resources, the signature is computed over a header whose
            # `nonce` has been replaced by its SHA-256. A standard RS256
            # verifier therefore cannot validate it *even with the correct
            # key*, which is exactly the state this branch is in.
            return (
                "Token signature verification failed: this token carries a "
                "`nonce` in its JWT header, which means Microsoft Entra issued "
                "it for one of its own resources (typically Microsoft Graph). "
                "Entra signs those over a modified header, so no third party "
                "can verify them -- this is by design and not a "
                "misconfiguration of this backend. Obtain a token audienced at "
                "this backend instead (Expose an API on the app registration, "
                "and note that the openEO Python client cannot request such a "
                "scope -- see docs/src/openid-connect.md). "
                f"Token facts: {facts}"
            )

        return (
            "Token signature verification failed: the key id was found in the "
            "provider's JWKS but did not verify this token. Check that the "
            "token comes from the configured provider and has not been "
            f"truncated or re-encoded in transit. Token facts: {facts}"
        )

    def _verify_claims(self, payload: Dict) -> None:
        """Check `iss`, `aud`/`azp`, `exp` and `nbf`. Raises `ValueError`."""
        # Issuer. Previously unchecked entirely, which meant a token from any
        # issuer whose signing key happened to be in the cached JWKS passed.
        expected_issuer = self.config.get("issuer")
        if expected_issuer and payload.get("iss") != expected_issuer:
            raise ValueError("Invalid issuer")

        # Audience. `client_id` covers ID tokens; `audiences` covers access
        # tokens audienced at an API instead of at the client (ADR 0006 S2.2).
        allowed = {self._oidc_config.client_id, *self._oidc_config.audiences}
        allowed.discard("")

        aud = payload.get("aud") or []
        aud_list = aud.split() if isinstance(aud, str) else list(aud)
        if not (payload.get("azp") in allowed or allowed & set(aud_list)):
            raise ValueError("Invalid audience")

        # Expiry. Now required: a token with no `exp` never expires, and
        # treating that as valid made the check optional in practice.
        now = time.time()
        exp = payload.get("exp")
        if exp is None:
            raise ValueError("Token has no 'exp' claim")
        if exp < now - _CLOCK_SKEW_LEEWAY:
            raise ValueError("Token expired")

        nbf = payload.get("nbf")
        if nbf is not None and nbf > now + _CLOCK_SKEW_LEEWAY:
            raise ValueError("Token is not yet valid")

    def login(self, authorization: str = Header()) -> Any:
        """OIDC doesn't support direct login - must be done through provider."""
        raise HTTPException(
            status_code=HTTP_401_UNAUTHORIZED,
            detail="OIDC authentication requires token from provider",
            headers={"WWW-Authenticate": "Bearer"},
        )

    def validate(self, authorization: str = Header()) -> User:
        """Validate Bearer Token."""
        if not authorization:
            raise HTTPException(
                status_code=HTTP_401_UNAUTHORIZED,
                detail="Authorization header missing",
                headers={"WWW-Authenticate": "Bearer"},
            )

        parsed_token = AuthToken.from_token(authorization)

        if parsed_token.method != self.method.value:
            raise HTTPException(
                status_code=HTTP_403_FORBIDDEN,
                detail="Invalid authentication method",
            )

        # Check the provider
        if parsed_token.provider != "oidc":
            raise HTTPException(
                status_code=HTTP_403_FORBIDDEN,
                detail="Invalid authentication provider",
            )

        try:
            # Get the key id from token header
            header_b64 = parsed_token.token.split(".")[0]
            header = json.loads(
                base64.urlsafe_b64decode(header_b64 + "=" * (-len(header_b64) % 4))
            )
            key = self._get_key(header["kid"])

            # Verify token and get payload
            payload = self._verify_token(parsed_token.token, key)

            # Create user from payload
            name_claim = None
            if self.settings.oidc and self.settings.oidc.name_claim:
                name_claim = payload.get(self.settings.oidc.name_claim)

            # Configurable because `sub` is pairwise on some providers and
            # every stored service is keyed on this value (ADR 0006 S2.4).
            id_claim = self._oidc_config.user_id_claim or "sub"
            user_id = payload.get(id_claim)
            if not user_id:
                raise ValueError(
                    f"Token has no {id_claim!r} claim to identify the user"
                )

            user = User(
                user_id=user_id,
                email=payload.get("email"),
                name=name_claim,
            )

            # Track user login
            self.store.track_user_login(user=user, provider="oidc")

            return user

        except HTTPException:
            # Already a considered response with its own detail. Re-wrapping it
            # produced messages like "401: 401:" -- the outer detail being
            # `str()` of the inner exception -- which hid the real cause.
            raise
        except Exception as err:
            raise HTTPException(
                status_code=HTTP_401_UNAUTHORIZED,
                # Some exceptions (notably cryptography's) stringify to "",
                # which would otherwise produce an empty, useless detail.
                detail=str(err) or f"{type(err).__name__} during token validation",
                headers={"WWW-Authenticate": "Bearer"},
            ) from err


class CredentialsBasic(BaseModel):
    """HTTP Basic Access Token."""

    access_token: str = Field(
        ...,
        json_schema_extra={
            "description": "The access token (without `basic//` prefix) to be used in the Bearer token for authorization in subsequent API calls."
        },
    )


class AuthToken(BaseModel):
    """The AuthToken breaks down the OpenEO token into its consituent parts to be used for validation."""

    method: Literal["basic", "oidc"]
    provider: Optional[str] = None
    token: str

    @field_validator("token")
    def check_token(cls, v):
        """Validate Token."""
        if v == "":
            raise ValueError("Empty token string.")
        return v

    @classmethod
    def from_token(cls, token: str) -> Self:
        """Takes the openeo format token, splits it into the component parts, and returns an Auth token."""

        if "Bearer " in token:
            token = token.removeprefix("Bearer ")

        return cls(**dict(zip(["method", "provider", "token"], token.split("/"))))  # type: ignore


@define(kw_only=True)
class BasicAuth(Auth):
    """Basic Auth implementation using AuthSettings."""

    method: AuthMethod = field(default=AuthMethod("basic"), init=False)
    # `factory` for the same reason as OIDCAuth.settings: a bare call here is
    # evaluated once, at import, against whatever environment happens to exist.
    settings: AuthSettings = field(factory=AuthSettings)

    def login(self, authorization: str = Header()) -> CredentialsBasic:
        """Validate Login credentials."""

        scheme, param = get_authorization_scheme_param(authorization)
        if scheme.lower() != "basic":
            raise HTTPException(
                status_code=HTTP_401_UNAUTHORIZED,
                detail="Invalid authentication scheme",
                headers={"WWW-Authenticate": "Basic"},
            )

        self._get_user_from_base64(param)
        return CredentialsBasic(access_token=param)

    def _get_user_from_base64(self, param: str) -> BasicAuthUser:
        try:
            data = b64decode(param).decode("ascii")
        except Exception as err:
            raise HTTPException(
                status_code=HTTP_401_UNAUTHORIZED,
                detail="Invalid base64 encoding",
                headers={"WWW-Authenticate": "Basic"},
            ) from err

        username, separator, password = data.partition(":")
        if not separator:
            raise HTTPException(
                status_code=HTTP_401_UNAUTHORIZED,
                detail="Invalid authentication credentials",
                headers={"WWW-Authenticate": "Basic"},
            )

        # Check if user exists and password matches
        user = self.settings.users.get(username)
        if not user or user["password"] != password:
            raise HTTPException(
                status_code=HTTP_401_UNAUTHORIZED,
                detail="Invalid username or password",
                headers={"WWW-Authenticate": "Basic"},
            )

        # return the user
        return BasicAuthUser(user_id=username, password=password)

    def validate(self, authorization: str = Header(default=None)) -> User:
        """Bearer Token or Basic Auth validation."""

        if not authorization:
            raise HTTPException(
                status_code=HTTP_401_UNAUTHORIZED,
                detail="Authorization header missing",
                headers={"WWW-Authenticate": "Bearer"},
            )

        parsed_token = AuthToken.from_token(authorization)

        if parsed_token.method != self.method.name:
            raise HTTPException(
                status_code=HTTP_403_FORBIDDEN,
                detail="Invalid authentication credentials",
            )

        base_user = self._get_user_from_base64(parsed_token.token)
        user = User(user_id=base_user.user_id)

        # Track user login
        self.store.track_user_login(user=user, provider="basic")

        return user
