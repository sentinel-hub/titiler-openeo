"""titiler.openeo errors.

This module implements the OpenEO API error handling specification.
See: https://api.openeo.org/#section/API-Principles/Error-Handling
"""

from typing import Optional

from fastapi import HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette import status


class OpenEOException(Exception):
    """Base class for OpenEO API exceptions."""

    def __init__(
        self,
        message: str,
        code: str,
        status_code: int = status.HTTP_400_BAD_REQUEST,
        id: Optional[str] = None,
        url: Optional[str] = None,
    ):
        """Initialize error with required OpenEO error fields.

        Args:
            message: Explains what went wrong and how to fix it
            code: Machine-readable error code
            status_code: HTTP status code
            id: Optional unique error instance identifier
            url: Optional URL with more information about the error
        """
        super().__init__(message)
        self.message = message
        self.code = code
        self.status_code = status_code
        self.id = id
        self.url = url

    def to_dict(self) -> dict:
        """Convert error to OpenEO API compliant dict format."""
        error = {
            "code": self.code,
            "message": self.message,
        }
        if self.id:
            error["id"] = self.id
        if self.url:
            error["url"] = self.url
        return error

    @staticmethod
    def openeo_exception_handler(
        request: Request, exc: "OpenEOException"
    ) -> JSONResponse:
        """Handle OpenEO exceptions."""
        return JSONResponse(
            status_code=exc.status_code,
            content=exc.to_dict(),
        )

    @staticmethod
    def validation_exception_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        """Handle FastAPI validation errors."""
        return JSONResponse(
            status_code=400,
            content={
                "code": "InvalidRequest",
                "message": str(exc),
            },
        )

    @staticmethod
    def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
        """Handle HTTP exceptions."""
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "code": "ServerError" if exc.status_code >= 500 else "InvalidRequest",
                "message": exc.detail,
            },
        )

    @staticmethod
    def general_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        """Handle general exceptions."""
        if isinstance(exc, ValueError):
            return OpenEOException.validation_exception_handler(request, exc)
        return JSONResponse(
            status_code=500,
            content={
                "code": "ServerError",
                "message": str(exc),
            },
        )


class ProcessParameterMissing(OpenEOException):
    """Invalid Parameters."""

    def __init__(self, parameter: str):
        """Initialize error with missing process parameter."""
        super().__init__(
            message=f"Required process parameter '{parameter}' is missing",
            code="ProcessParameterMissing",
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        )


class NoDataAvailable(OpenEOException):
    """No data available for the requested extent."""

    def __init__(
        self, message: str = "There is no data available for the given extents"
    ):
        """Initialize error with no data available."""
        super().__init__(
            message=message,
            code="NoDataAvailable",
            status_code=status.HTTP_404_NOT_FOUND,
        )


class InvalidProcessGraph(OpenEOException):
    """The process graph is invalid."""

    def __init__(self, message: str):
        """Initialize error with invalid process graph."""
        super().__init__(
            message=message,
            code="InvalidProcessGraph",
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        )


class TemporalExtentEmpty(OpenEOException):
    """Invalid temporal extent."""

    def __init__(self):
        """Initialize error with empty temporal extent."""
        super().__init__(
            message="The temporal extent is empty. The second instant in time must be greater/later than the first instant in time",
            code="TemporalExtentEmpty",
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        )


class AuthenticationRequired(OpenEOException):
    """Authentication is required."""

    def __init__(self):
        """Initialize error with authentication required."""
        super().__init__(
            message="Authentication is required to access this resource",
            code="AuthenticationRequired",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )


class AuthenticationFailed(OpenEOException):
    """Authentication failed."""

    def __init__(self):
        """Initialize error with authentication failed."""
        super().__init__(
            message="The provided credentials are invalid",
            code="AuthenticationFailed",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )


class AccessDenied(OpenEOException):
    """Access to the resource is forbidden."""

    def __init__(self):
        """Initialize error with access denied."""
        super().__init__(
            message="You don't have permission to access this resource",
            code="AccessDenied",
            status_code=status.HTTP_403_FORBIDDEN,
        )


class ResourceNotFound(OpenEOException):
    """The requested resource was not found."""

    def __init__(self, resource_type: str, resource_id: str):
        """Initialize error with resource not found."""
        super().__init__(
            message=f"The requested {resource_type} with id '{resource_id}' does not exist",
            code="ResourceNotFound",
            status_code=status.HTTP_404_NOT_FOUND,
        )


class ServiceUnavailable(OpenEOException):
    """The service is temporarily unavailable."""

    def __init__(self, detail: str = "The service is temporarily unavailable"):
        """Initialize error with service unavailable."""
        super().__init__(
            message=detail,
            code="ServiceUnavailable",
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        )
