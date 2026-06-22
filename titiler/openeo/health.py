"""Health-check endpoints for titiler-openeo.

Implements two distinct probes following Kubernetes semantics:

* ``GET /healthz`` -- liveness. Cheap, dependency-free; always returns 200
  as long as the FastAPI event loop is responsive. Suitable for a
  ``livenessProbe``.

* ``GET /readyz`` -- readiness. Runs a bounded-timeout probe against every
  configured backend (services store, optional tile store, STAC API and -
  when OIDC is in use - the OIDC well-known endpoint). Returns 200 only
  when every check passes; otherwise 503 with a structured body listing
  which check failed. Suitable for a ``readinessProbe``.

To avoid hammering backends, configure the Kubernetes probe with a
suitable ``periodSeconds`` (the bundled Helm chart uses 30 s).
"""

from __future__ import annotations

import concurrent.futures
import logging
import time
from typing import Any, Callable, Dict, List, Optional

from fastapi import APIRouter, FastAPI
from fastapi.responses import JSONResponse

from . import __version__ as titiler_version
from .settings import HealthSettings

logger = logging.getLogger(__name__)

# Background executor used to enforce per-check timeouts. ``max_workers`` is
# small because we only ever run a handful of checks concurrently per probe.
_executor = concurrent.futures.ThreadPoolExecutor(
    max_workers=4, thread_name_prefix="readyz-check"
)


def _run_check(name: str, fn: Callable[[], Any], timeout: float) -> Dict[str, Any]:
    """Run a single dependency check with a bounded timeout.

    Returns a small dict suitable for inclusion in the ``checks`` field of
    the readiness response. Never raises.
    """
    start = time.monotonic()
    future = _executor.submit(fn)
    try:
        future.result(timeout=timeout)
    except concurrent.futures.TimeoutError:
        future.cancel()
        return {
            "status": "error",
            "error": f"timeout after {int(timeout * 1000)}ms",
        }
    except Exception as err:  # pragma: no cover - defensive
        logger.warning("readiness check %s failed: %s", name, err)
        return {"status": "error", "error": str(err) or err.__class__.__name__}
    latency_ms = int((time.monotonic() - start) * 1000)
    return {"status": "ok", "latency_ms": latency_ms}


def _build_checks(
    *,
    service_store: Any,
    tile_store: Any,
    stac_client: Any,
    auth: Any,
) -> List[tuple]:
    """Return a list of ``(name, callable)`` checks to run for /readyz."""
    checks: List[tuple] = []

    if service_store is not None and hasattr(service_store, "ping"):
        checks.append(("store", service_store.ping))

    if tile_store is not None and hasattr(tile_store, "ping"):
        checks.append(("tile_store", tile_store.ping))

    if stac_client is not None and hasattr(stac_client, "ping"):
        checks.append(("stac_api", stac_client.ping))

    # Only probe OIDC when the auth method is actually OIDC -- otherwise the
    # well-known URL is irrelevant to readiness.
    if (
        auth is not None
        and getattr(getattr(auth, "method", None), "value", None) == "oidc"
        and hasattr(auth, "ping")
    ):
        checks.append(("auth_oidc", auth.ping))

    return checks


def register_health_endpoints(
    app: FastAPI,
    *,
    service_store: Any = None,
    tile_store: Any = None,
    stac_client: Any = None,
    auth: Any = None,
    settings: Optional[HealthSettings] = None,
) -> None:
    """Mount ``/healthz`` and ``/readyz`` routes on ``app``.

    Routes are excluded from the OpenAPI schema and tagged ``health`` so
    they don't clutter the generated docs.
    """
    settings = settings or HealthSettings()

    router = APIRouter(tags=["health"], include_in_schema=False)

    @router.get("/healthz")
    async def healthz() -> Dict[str, str]:
        """Liveness probe. Always returns 200 if the event loop is responsive.

        Declared ``async`` on purpose: a sync handler is dispatched to the shared
        anyio threadpool, which the (sync) compute endpoints saturate under load,
        starving the probe and triggering needless liveness restarts. As an async
        no-op this runs directly on the event loop and stays responsive
        regardless of threadpool/compute load.
        """
        return {"status": "ok"}

    @router.get("/readyz")
    def readyz() -> JSONResponse:
        """Readiness probe. 200 when every configured dependency is healthy."""
        checks = _build_checks(
            service_store=service_store,
            tile_store=tile_store,
            stac_client=stac_client,
            auth=auth,
        )

        results: Dict[str, Dict[str, Any]] = {}
        all_ok = True
        for name, fn in checks:
            result = _run_check(name, fn, timeout=settings.check_timeout)
            results[name] = result
            if result["status"] != "ok":
                all_ok = False

        payload: Dict[str, Any] = {
            "status": "ok" if all_ok else "degraded",
            "version": titiler_version,
            "checks": results,
        }
        status_code = 200 if all_ok else 503
        return JSONResponse(status_code=status_code, content=payload)

    app.include_router(router)
