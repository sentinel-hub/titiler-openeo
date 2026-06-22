"""Tests for the /healthz and /readyz endpoints."""

import time


def test_healthz(app_no_auth):
    """/healthz is dependency-free and always returns 200."""
    response = app_no_auth.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_healthz_handler_is_async(app_no_auth):
    """Regression: /healthz must be an async handler.

    A sync handler is dispatched to the shared anyio threadpool, which the sync
    compute endpoints saturate under load, starving the liveness probe and
    causing needless pod restarts. Keeping it async runs it on the event loop,
    decoupled from threadpool/compute load.
    """
    import inspect

    endpoint = next(
        r.endpoint
        for r in app_no_auth.app.routes
        if getattr(r, "path", None) == "/healthz"
    )
    assert inspect.iscoroutinefunction(endpoint), "/healthz must be async def"


def test_healthz_not_in_openapi(app_no_auth):
    """Health endpoints must not pollute the OpenAPI document."""
    openapi = app_no_auth.get("/api").json()
    paths = openapi.get("paths", {})
    assert "/healthz" not in paths
    assert "/readyz" not in paths


def test_readyz_ok(app_no_auth, monkeypatch):
    """/readyz returns 200 when every configured dependency pings cleanly."""
    # Replace the STAC API ping with a no-op so this test never hits the
    # network. The local services store ping is exercised for real.
    from titiler.openeo.stacapi import stacApiBackend

    monkeypatch.setattr(stacApiBackend, "ping", lambda self, *a, **kw: None)

    response = app_no_auth.get("/readyz")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert "version" in body
    assert "store" in body["checks"]
    assert body["checks"]["store"]["status"] == "ok"
    assert "stac_api" in body["checks"]
    assert body["checks"]["stac_api"]["status"] == "ok"
    # OIDC must NOT appear when auth method is basic.
    assert "auth_oidc" not in body["checks"]
    # Tile store is only configured when TITILER_OPENEO_TILE_STORE_URL is set.
    assert "tile_store" not in body["checks"]


def test_readyz_reports_failed_check(app_no_auth, monkeypatch):
    """/readyz returns 503 with a descriptive error when a check fails."""
    from titiler.openeo.stacapi import stacApiBackend

    def boom(self) -> None:
        raise RuntimeError("backend down")

    monkeypatch.setattr(stacApiBackend, "ping", boom)

    response = app_no_auth.get("/readyz")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "degraded"
    assert body["checks"]["stac_api"]["status"] == "error"
    assert "backend down" in body["checks"]["stac_api"]["error"]


def test_run_check_timeout():
    """The per-check timeout is enforced and reported as a timeout error."""
    from titiler.openeo.health import _run_check

    def slow() -> None:
        time.sleep(1.0)

    result = _run_check("slow", slow, timeout=0.05)
    assert result["status"] == "error"
    assert "timeout" in result["error"]


def test_run_check_success():
    """A fast successful check reports latency."""
    from titiler.openeo.health import _run_check

    result = _run_check("noop", lambda: None, timeout=1.0)
    assert result["status"] == "ok"
    assert "latency_ms" in result


def test_readyz_includes_tile_store_when_configured(app_no_auth, monkeypatch):
    """When a tile store is configured, the tile_store check is included."""
    from titiler.openeo import main as main_module
    from titiler.openeo.stacapi import stacApiBackend

    monkeypatch.setattr(stacApiBackend, "ping", lambda self, *a, **kw: None)

    class _FakeTileStore:
        def ping(self) -> None:
            return None

    # The app was already built without a tile store; rebuild it with one
    # injected via the module-level singleton that ``create_app`` reads.
    monkeypatch.setattr(main_module, "tile_store", _FakeTileStore())

    from starlette.testclient import TestClient

    client = TestClient(main_module.create_app())
    response = client.get("/readyz")
    assert response.status_code == 200
    body = response.json()
    assert body["checks"]["tile_store"]["status"] == "ok"
