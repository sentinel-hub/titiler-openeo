"""Test the cooperative cancellation of request work."""

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

import anyio
import numpy as np
import pytest
from rio_tiler.models import ImageData
from starlette.requests import Request

from titiler.openeo import cancellation
from titiler.openeo.cancellation import (
    RequestCancelled,
    raise_if_cancelled,
    run_cancellable,
    submit_in_context,
)
from titiler.openeo.factory import EndpointsFactory
from titiler.openeo.processes.implementations.data_model import RasterStack


def _image() -> ImageData:
    return ImageData(np.ma.MaskedArray(np.ones((1, 4, 4))))


def _receive_disconnect_after(delay: float):
    """ASGI receive: the request body, then a disconnect after ``delay``.

    Like uvicorn: after the disconnect, ``receive`` returns at once. Before it,
    ``receive`` waits. ``Request.is_disconnected`` relies on this, because it
    calls ``receive`` in a cancelled scope.
    """
    messages = [{"type": "http.request", "body": b"", "more_body": False}]
    start = time.monotonic()

    async def receive():
        if messages:
            return messages.pop(0)
        remaining = delay - (time.monotonic() - start)
        if remaining > 0:
            await anyio.sleep(remaining)
        return {"type": "http.disconnect"}

    return receive


def _request(receive) -> Request:
    return Request({"type": "http", "method": "GET", "headers": []}, receive)


def _slow_checkpointed_work(calls: list, steps: int = 50, step: float = 0.05):
    """Work of ``steps`` units, with a checkpoint before each unit."""
    for _ in range(steps):
        raise_if_cancelled()
        calls.append(1)
        time.sleep(step)
    return "done"


def test_raise_if_cancelled_without_request_does_nothing():
    raise_if_cancelled()


def test_raise_if_cancelled_when_flag_set():
    event = threading.Event()
    token = cancellation._cancel_event.set(event)
    try:
        raise_if_cancelled()
        event.set()
        with pytest.raises(RequestCancelled):
            raise_if_cancelled()
    finally:
        cancellation._cancel_event.reset(token)


def test_submit_in_context_sends_flag_to_worker_thread():
    event = threading.Event()
    event.set()
    token = cancellation._cancel_event.set(event)
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            plain = executor.submit(raise_if_cancelled)
            in_context = submit_in_context(executor, raise_if_cancelled)
            # A plain submit does not see the flag of the request.
            assert plain.result() is None
            with pytest.raises(RequestCancelled):
                in_context.result()
    finally:
        cancellation._cancel_event.reset(token)


def test_raster_stack_stops_queued_reads_when_cancelled():
    event = threading.Event()
    calls = []

    def task():
        calls.append(1)
        # The client goes away during the first read.
        event.set()
        time.sleep(0.05)
        return _image()

    start = datetime(2021, 1, 1)
    tasks = [(task, {"datetime": start + timedelta(days=i)}) for i in range(20)]
    stack = RasterStack(
        tasks=tasks, timestamp_fn=lambda asset: asset["datetime"], max_workers=2
    )

    token = cancellation._cancel_event.set(event)
    try:
        with pytest.raises(RequestCancelled):
            stack.prefetch(stack.timestamps())
    finally:
        cancellation._cancel_event.reset(token)

    # Only the reads that started before the flag was set ran.
    assert len(calls) <= 2


def test_run_cancellable_returns_result():
    async def main():
        receive = _receive_disconnect_after(10)
        return await run_cancellable(_request(receive), lambda: "tile")

    assert anyio.run(main) == "tile"


def test_run_cancellable_raises_exception_of_fn_unwrapped():
    def fail():
        raise ValueError("bad tile")

    async def main():
        await run_cancellable(_request(_receive_disconnect_after(10)), fail)

    # Not an ExceptionGroup: the FastAPI exception handlers must match it.
    with pytest.raises(ValueError, match="bad tile"):
        anyio.run(main)


def test_run_cancellable_stops_work_on_disconnect():
    calls: list = []

    async def main():
        receive = _receive_disconnect_after(0.2)
        await run_cancellable(_request(receive), lambda: _slow_checkpointed_work(calls))

    start = time.monotonic()
    with pytest.raises(RequestCancelled):
        anyio.run(main)

    # Without cancellation, the work takes 50 * 0.05 = 2.5 s.
    assert time.monotonic() - start < 1.0
    assert len(calls) < 50


def test_xyz_tile_returns_499_on_disconnect(app_no_auth, monkeypatch):
    calls: list = []

    def render(self, request, service_id, z, x, y, user):
        return _slow_checkpointed_work(calls)

    monkeypatch.setattr(EndpointsFactory, "_render_xyz_tile", render)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/services/xyz/some-service/tiles/1/0/0",
        "raw_path": b"/services/xyz/some-service/tiles/1/0/0",
        "query_string": b"",
        "root_path": "",
        "headers": [(b"host", b"testserver")],
        "client": ("testclient", 123),
        "server": ("testserver", 80),
    }
    sent = []

    async def send(message):
        sent.append(message)

    async def main():
        await app_no_auth.app(scope, _receive_disconnect_after(0.2), send)

    start = time.monotonic()
    anyio.run(main)

    assert time.monotonic() - start < 1.0
    assert len(calls) < 50
    status = next(m["status"] for m in sent if m["type"] == "http.response.start")
    assert status == 499
