"""Tests for ExceptionHandler: openEO-spec-compliant error responses.

See https://api.openeo.org/#section/API-Principles/Error-Handling and the
standardized error-code registry at
https://github.com/Open-EO/openeo-api/blob/master/errors.json.
"""

import json
import logging

import pytest

from titiler.openeo.errors import (
    ExceptionHandler,
    ProcessParameterInvalid,
    ProcessParameterRequired,
)


@pytest.fixture
def handler():
    return ExceptionHandler(logger=logging.getLogger("test"))


def _body(response):
    return json.loads(response.body)


class TestOpenEOExceptionHandler:
    """A specific OpenEOException always yields its own code and status."""

    def test_process_parameter_invalid(self, handler):
        """A bad parameter value/type yields code=ProcessParameterInvalid, status=400."""
        exc = ProcessParameterInvalid(
            "Parameter 'x' in process 'requires_int': expected 'integer' but got 'string'"
        )
        response = handler.openeo_exception_handler(None, exc)

        assert response.status_code == 400
        assert _body(response) == {
            "code": "ProcessParameterInvalid",
            "message": "Parameter 'x' in process 'requires_int': expected 'integer' but got 'string'",
        }

    def test_process_parameter_required(self, handler):
        """A missing required parameter yields code=ProcessParameterRequired, status=400."""
        exc = ProcessParameterRequired("bbox")
        response = handler.openeo_exception_handler(None, exc)

        assert response.status_code == 400
        body = _body(response)
        assert body["code"] == "ProcessParameterRequired"
        assert "bbox" in body["message"]


class TestGeneralExceptionHandler:
    """The catch-all is a safety net for exceptions not raised as OpenEOException."""

    def test_bare_value_error_is_client_error(self, handler):
        """A ValueError not yet raised as an OpenEOException still reports 400."""
        response = handler.general_exception_handler(None, ValueError("bad value"))

        assert response.status_code == 400
        assert _body(response) == {"code": "InvalidRequest", "message": "bad value"}

    def test_bare_type_error_is_client_error(self, handler):
        """A TypeError that slipped through unwrapped still reports 400, not 500."""
        response = handler.general_exception_handler(None, TypeError("bad type"))

        assert response.status_code == 400
        assert _body(response) == {"code": "InvalidRequest", "message": "bad type"}

    def test_genuine_internal_error_uses_standardized_code(self, handler):
        """A real server fault uses the registered `Internal` code, not a proprietary one."""
        response = handler.general_exception_handler(None, RuntimeError("disk full"))

        assert response.status_code == 500
        body = _body(response)
        assert body["code"] == "Internal"
        assert body["message"] == "Server error: disk full"
