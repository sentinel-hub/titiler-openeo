"""Test the cooperative cancellation of request work."""

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

import anyio
import numpy as np
import pytest
import rasterio
from openeo_pg_parser_networkx.pg_schema import BoundingBox
from pystac import Item
from rasterio.transform import from_origin
from rio_tiler.models import ImageData
from starlette.requests import Request

from titiler.openeo import cancellation
from titiler.openeo.cancellation import (
    RequestCancelled,
    cancellable_sleep,
    raise_if_cancelled,
    run_cancellable,
    submit_in_context,
)
from titiler.openeo.factory import EndpointsFactory
from titiler.openeo.processes.implementations import io as io_module
from titiler.openeo.processes.implementations.data_model import RasterStack
from titiler.openeo.stacapi import LoadStac

ITEM = {
    "type": "Feature",
    "id": "item",
    "stac_version": "1.0.0",
    "stac_extensions": [
        "https://stac-extensions.github.io/projection/v1.1.0/schema.json"
    ],
    "bbox": [0, 0, 1, 1],
    "geometry": {
        "type": "Polygon",
        "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]],
    },
    "properties": {
        "datetime": "2021-01-01T00:00:00Z",
        "proj:crs": "EPSG:4326",
        "proj:transform": [0.0002, 0.0, 0.0, 0.0, -0.0002, 0.0],
    },
    "assets": {"B01": {"href": "https://example.com/B01.tif", "type": "image/tiff"}},
    "links": [],
}


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


def test_cancellable_sleep_without_request_sleeps():
    start = time.monotonic()
    cancellable_sleep(0.05)
    assert time.monotonic() - start >= 0.05


def test_cancellable_sleep_wakes_on_cancel():
    event = threading.Event()
    token = cancellation._cancel_event.set(event)
    try:
        threading.Timer(0.1, event.set).start()
        start = time.monotonic()
        with pytest.raises(RequestCancelled):
            cancellable_sleep(10)
        assert time.monotonic() - start < 1.0
    finally:
        cancellation._cancel_event.reset(token)


def test_run_cancellable_stops_work_when_parent_scope_cancelled():
    calls: list = []

    async def main():
        # No disconnect: the enclosing scope is cancelled (e.g. shutdown).
        with anyio.move_on_after(0.2):
            await run_cancellable(
                _request(_receive_disconnect_after(10)),
                lambda: _slow_checkpointed_work(calls),
            )

    start = time.monotonic()
    anyio.run(main)

    assert time.monotonic() - start < 1.0
    assert len(calls) < 50


def test_run_cancellable_does_not_start_work_after_disconnect(monkeypatch):
    calls: list = []
    run_sync = anyio.to_thread.run_sync

    async def run_sync_after_wait(fn, *args, **kwargs):
        # The request waits for a free worker thread; the client goes away.
        await anyio.sleep(0.3)
        return await run_sync(fn, *args, **kwargs)

    monkeypatch.setattr(anyio.to_thread, "run_sync", run_sync_after_wait)

    async def main():
        await run_cancellable(
            _request(_receive_disconnect_after(0.05)), lambda: calls.append(1)
        )

    with pytest.raises(RequestCancelled):
        anyio.run(main)
    assert calls == []


def test_load_stac_reads_lazily_with_request_context(monkeypatch):
    """load_stac tasks run where the stack is read, so they see the flag."""
    seen = []

    def reader(item, bbox, **kwargs):
        seen.append(cancellation._cancel_event.get())
        return ImageData(
            np.ma.ones((1, 10, 10), dtype="float32"),
            crs=kwargs["dst_crs"],
            bounds=tuple(bbox),
        )

    item = Item.from_dict(ITEM)
    monkeypatch.setattr(LoadStac, "_load_stac_object", lambda self, url: item)
    monkeypatch.setattr("titiler.openeo.stacapi._reader", reader)

    stack = LoadStac().load_stac(
        url="https://example.com/item.json",
        spatial_extent=BoundingBox(west=0, south=0, east=1, north=1),
        bands=["B01"],
        width=10,
        height=10,
    )
    # Nothing is read at load time.
    assert seen == []

    event = threading.Event()
    token = cancellation._cancel_event.set(event)
    try:
        _ = stack.first
    finally:
        cancellation._cancel_event.reset(token)
    assert seen == [event]


@pytest.fixture
def local_cog(tmp_path):
    """A small single-band GeoTIFF."""
    path = tmp_path / "tiny.tif"
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=10,
        height=10,
        count=1,
        dtype="uint16",
        crs="EPSG:32631",
        transform=from_origin(500000, 5000000, 10, 10),
    ) as dst:
        dst.write(np.arange(100, dtype="uint16").reshape(1, 10, 10))
    return str(path)


def test_load_url_reads_lazily_with_request_context(monkeypatch, local_cog):
    """load_url reads where the stack is read, so its read sees the flag."""
    seen = []
    real_reader = io_module._reader

    def reader(*args, **kwargs):
        seen.append(cancellation._cancel_event.get())
        return real_reader(*args, **kwargs)

    monkeypatch.setattr(io_module, "_reader", reader)

    stack = io_module.load_url(local_cog)
    # Nothing is read at load time.
    assert seen == []

    event = threading.Event()
    token = cancellation._cancel_event.set(event)
    try:
        _ = stack.first
    finally:
        cancellation._cancel_event.reset(token)
    assert seen == [event]


def test_load_url_read_stops_when_cancelled(local_cog):
    stack = io_module.load_url(local_cog)

    event = threading.Event()
    event.set()
    token = cancellation._cancel_event.set(event)
    try:
        with pytest.raises(RequestCancelled):
            _ = stack.first
    finally:
        cancellation._cancel_event.reset(token)
