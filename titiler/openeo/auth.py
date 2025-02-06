"""titiler.openeo.auth."""

import abc
from base64 import b64decode
from enum import Enum
from typing import Any, Literal, Optional

from attrs import define, field
from .settings import AuthSettings
from fastapi import Header
from fastapi.exceptions import HTTPException
from fastapi.security.utils import get_authorization_scheme_param
from pydantic import BaseModel, Field, ValidationError, field_validator
from starlette.status import HTTP_401_UNAUTHORIZED, HTTP_403_FORBIDDEN
from typing_extensions import Self


def get_auth(settings: AuthSettings) -> 'Auth':
    """Get Auth instance."""
    if settings.method == AuthMethod.basic.value:
        return BasicAuth(settings=settings)
    else:
        raise NotImplementedError(f"Auth method {settings.method} not implemented")


class AuthMethod(Enum):
    """Authentication Method."""

    basic = "basic"
    oidc = "oidc"


class User(BaseModel, extra="allow"):
    """User Model."""

    user_id: str


class BasicAuthUser(User):
    """Basic Auth User Model."""

    password: str


@define(kw_only=True)
class Auth(metaclass=abc.ABCMeta):
    """Auth BaseClass."""

    method: AuthMethod = field(init=False)

    @abc.abstractmethod
    def login(self, authorization: str = Header()) -> Any:
        """Validate login and/or create a new user."""
        ...

    @abc.abstractmethod
    def validate(self, authorization: str = Header()) -> User:
        """Validate Bearer Token."""
        ...


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

    # @field_validator("provider")
    # def check_provider(cls, v):
    #     if not v:
    #         raise ValidationError("Empty provider string.")
    #     return v

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
    settings: AuthSettings = field()

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
        except Exception:
            raise HTTPException(  # noqa: B904
                status_code=HTTP_401_UNAUTHORIZED,
                detail="Invalid base64 encoding",
                headers={"WWW-Authenticate": "Basic"},
            )

        username, separator, password = data.partition(":")
        if not separator:
            raise HTTPException(
                status_code=HTTP_401_UNAUTHORIZED,
                detail="Invalid authentication credentials",
                headers={"WWW-Authenticate": "Basic"},
            )

        # Check if user exists and password matches
        user = self.settings.users.get(username)
        if not user or user.password != password:
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

        if parsed_token.method != self.method:
            raise HTTPException(
                status_code=HTTP_403_FORBIDDEN,
                detail="Invalid authentication credentials",
            )

        new_authorization = f"basic {parsed_token.token}"
        user = self._get_user_from_base64(new_authorization)
        return User(user_id=user.user_id)
