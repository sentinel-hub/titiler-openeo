"""Tests for ExceptionHandler: openEO-spec-compliant error responses.

See https://api.openeo.org/#section/API-Principles/Error-Handling and the
standardized error-code registry at
https://github.com/Open-EO/openeo-api/blob/master/errors.json.
"""

import base64
import json
import logging

import pytest
from fastapi import HTTPException
from starlette.testclient import TestClient

from titiler.openeo.errors import (
    ExceptionHandler,
    FeatureUnsupported,
    ProcessParameterInvalid,
    ProcessParameterRequired,
    ServiceUnavailable,
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


class TestServerErrorLogging:
    """A 5xx is a fault to look into: ERROR, with the traceback."""

    @pytest.mark.parametrize(
        "call, exc",
        [
            ("http_exception_handler", HTTPException(500, "boom")),
            ("http_exception_handler", HTTPException(503, "down")),
            ("openeo_exception_handler", FeatureUnsupported("not here")),
            ("openeo_exception_handler", ServiceUnavailable()),
        ],
        ids=["http-500", "http-503", "openeo-501", "openeo-503"],
    )
    def test_logged_with_traceback(self, handler, caplog, call, exc):
        """Each 5xx handler call gives one ERROR record carrying the exception."""
        with caplog.at_level(logging.DEBUG, logger="test"):
            getattr(handler, call)(None, exc)

        [record] = caplog.records
        assert record.levelno == logging.ERROR
        assert record.exc_info[1] is exc


# These tests are about logging, not storage: one store is enough.
@pytest.mark.parametrize("store_type", ["local"])
class TestClientErrorLogging:
    """A client error (4xx) through the app is one WARNING line with its status.

    Wrong credentials and bad requests are expected traffic: logged at ERROR
    with a traceback, they bury the real faults.
    """

    @staticmethod
    def _assert_client_error_logged(caplog, prefix, status):
        records = [r for r in caplog.records if r.name.startswith("titiler.openeo")]
        assert [r for r in records if r.levelno >= logging.ERROR or r.exc_info] == []
        [record] = [r for r in records if r.getMessage().startswith(prefix)]
        assert record.levelno == logging.WARNING
        assert record.getMessage().endswith(f"(status {status})")
        assert "\n" not in record.getMessage()

    def test_wrong_basic_auth_password(self, main_module, caplog):
        """A 4xx HTTPException, and the password is not logged."""
        client = TestClient(main_module.create_app())
        # `main_module` sets up basic auth with the user "test".
        wrong = base64.b64encode(b"test:not-the-password").decode()
        with caplog.at_level(logging.DEBUG, logger="titiler.openeo"):
            response = client.get(
                "/credentials/basic", headers={"Authorization": f"Basic {wrong}"}
            )

        assert response.status_code == 401
        assert response.json()["message"] == "Invalid username or password"
        self._assert_client_error_logged(caplog, "HTTP Exception:", 401)
        assert "not-the-password" not in caplog.text
        assert wrong not in caplog.text

    def test_invalid_process_graph(self, app_with_auth, caplog):
        """A 4xx OpenEOException."""
        body = {
            "id": "udp-bad",
            "process_graph": {
                "node1": {
                    "process_id": "does_not_exist",
                    "arguments": {},
                    "result": True,
                }
            },
        }
        with caplog.at_level(logging.DEBUG, logger="titiler.openeo"):
            response = app_with_auth.put("/process_graphs/udp-bad", json=body)

        assert response.status_code == 422
        assert response.json()["code"] == "InvalidProcessGraph"
        self._assert_client_error_logged(
            caplog, "OpenEO Exception InvalidProcessGraph:", 422
        )

    def test_request_validation_error(self, app_with_auth, caplog):
        """A request that fails FastAPI's validation (answered with 400)."""
        with caplog.at_level(logging.DEBUG, logger="titiler.openeo"):
            response = app_with_auth.get("/process_graphs", params={"limit": "many"})

        assert response.status_code == 400
        assert response.json()["code"] == "InvalidRequest"
        self._assert_client_error_logged(caplog, "Validation Error:", 400)
