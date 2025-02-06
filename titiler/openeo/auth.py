"""titiler.openeo.auth."""

import abc
from base64 import b64decode
from typing import Any, Literal, Optional

from attrs import define, field
from fastapi import Header
from fastapi.exceptions import HTTPException
from fastapi.security.utils import get_authorization_scheme_param
from pydantic import BaseModel, Field, ValidationError, field_validator
from starlette.status import HTTP_401_UNAUTHORIZED, HTTP_403_FORBIDDEN
from typing_extensions import Self


class User(BaseModel, extra="allow"):
    """User Model."""

    user_id: str


class AuthToken(BaseModel):
    """The AuthToken breaks down the OpenEO token into its consituent parts to be used for validation."""

    method: Literal["basic", "oidc"]
    provider: Optional[str] = None  # TODO: optional?
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
class Auth(metaclass=abc.ABCMeta):
    """Auth BaseClass."""

    method: Literal["basic", "oidc"] = field(init=False)

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


@define(kw_only=True)
class FakeBasicAuth(Auth):
    """BasicAuth."""

    method: Literal["basic", "oidc"] = field(default="basic", init=False)

    def login(self, authorization: str = Header()) -> CredentialsBasic:
        """Validate Login credentials."""
        scheme, param = get_authorization_scheme_param(authorization)
        data = b64decode(param).decode("ascii")

        username, separator, password = data.partition(":")
        if not separator:
            raise HTTPException(
                status_code=HTTP_401_UNAUTHORIZED,
                detail="Invalid authentication credentials",
                headers={"WWW-Authenticate": "Basic"},
            )

        # FAKE Username/TOKEN
        if username == "anonymous":
            return CredentialsBasic(access_token="yo")

        raise HTTPException(
            status_code=HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials",
            headers={"WWW-Authenticate": "Basic"},
        )

    def validate(self, authorization: str = Header(default=None)) -> User:
        """Bearer Token."""
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

        # FAKE Username/TOKEN
        if parsed_token.token == "yo":
            return User(user_id="12d6b89f-0f26-4fe7-a461-67418919b794")

        raise HTTPException(
            status_code=HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials",
            headers={"WWW-Authenticate": "Basic"},
        )
