"""titiler.openeo.auth."""

import abc
import base64
import json
import time
from base64 import b64decode
from enum import Enum
from typing import Any, Dict, Literal, Optional

import httpx
from attrs import define, field
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicNumbers
from fastapi import Header
from fastapi.exceptions import HTTPException
from fastapi.security.utils import get_authorization_scheme_param
from pydantic import BaseModel, Field, ValidationError, field_validator
from starlette.status import HTTP_401_UNAUTHORIZED, HTTP_403_FORBIDDEN
from typing_extensions import Self

from .models.auth import BasicAuthUser, User
from .services.base import ServicesStore
from .settings import AuthSettings, OIDCConfig


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
    settings: AuthSettings = field(default=AuthSettings())
    _config_cache: Optional[Dict] = field(default=None, init=False)
    _jwks_cache: Optional[Dict] = field(default=None, init=False)
    _oidc_config: OIDCConfig = field(init=False)

    def __attrs_post_init__(self):
        """Validate OIDC configuration on initialization."""
        if not self.settings.oidc:
            raise ValueError("OIDC configuration required")
        self._oidc_config = self.settings.oidc

    @property
    def config(self) -> Dict:
        """Get OIDC configuration."""
        if self._config_cache is None:
            with httpx.Client() as client:
                response = client.get(str(self._oidc_config.wk_url))
                response.raise_for_status()
                self._config_cache = response.json()
        return self._config_cache

    def get_jwks(self) -> Dict:
        """Get JSON Web Key Set."""
        if self._jwks_cache is None:
            with httpx.Client() as client:
                response = client.get(self.config["jwks_uri"])
                response.raise_for_status()
                self._jwks_cache = response.json()
        return self._jwks_cache

    def _get_key(self, kid: str):
        """Get public key from JWKS."""
        jwks = self.get_jwks()
        for jwk in jwks["keys"]:
            if jwk["kid"] == kid:
                if jwk["kty"] != "RSA":
                    raise ValueError(f"Unsupported key type: {jwk['kty']}")

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

        raise HTTPException(
            status_code=HTTP_401_UNAUTHORIZED,
            detail="Unable to find appropriate key",
        )

    def _verify_token(self, token: str, key) -> Dict:
        """Verify JWT token signature and return payload."""
        try:
            # Split the JWT
            header_b64, payload_b64, signature_b64 = token.split(".")

            # Decode header and payload
            json.loads(
                base64.urlsafe_b64decode(header_b64 + "=" * (-len(header_b64) % 4))
            )
            payload = json.loads(
                base64.urlsafe_b64decode(payload_b64 + "=" * (-len(payload_b64) % 4))
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

            # Verify claims
            if not (
                payload.get("azp") == self._oidc_config.client_id
                or self._oidc_config.client_id in payload.get("aud").split(" ")
            ):
                raise ValueError("Invalid audience")

            current_time = time.time()
            if payload.get("exp") and payload["exp"] < current_time:
                raise ValueError("Token expired")

            return payload

        except (ValueError, InvalidSignature) as err:
            raise HTTPException(
                status_code=HTTP_401_UNAUTHORIZED,
                detail=str(err),
            ) from err

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

            user = User(
                user_id=payload["sub"],
                email=payload.get("email"),
                name=name_claim,
            )

            # Track user login
            self.store.track_user_login(user=user, provider="oidc")

            return user

        except Exception as err:
            raise HTTPException(
                status_code=HTTP_401_UNAUTHORIZED,
                detail=str(err),
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
            raise ValidationError("Empty token string.")
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
    settings: AuthSettings = field(default=AuthSettings())

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
