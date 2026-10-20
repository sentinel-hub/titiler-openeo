"""Cooperative cancellation of request work.

A thread cannot be stopped from outside. When a client goes away (for example a
map viewer that drops a tile when the user pans), the request thread continues
until the work is done. To free the resources early, ``run_cancellable`` sets a
flag when the client disconnects, and the read path calls ``raise_if_cancelled``
between units of work (one item read, one stack slice).

The flag is a ``threading.Event`` in a ``ContextVar``. Outside a request (CLI,
scripts, tests) there is no flag and ``raise_if_cancelled`` does nothing.
"""

import contextvars
import logging
import threading
import time
from concurrent.futures import Executor, Future
from typing import Any, Callable, Optional, TypeVar

import anyio
import anyio.lowlevel
from starlette.requests import Request

logger = logging.getLogger(__name__)

T = TypeVar("T")

#: Interval, in seconds, between two checks of the client connection.
DISCONNECT_POLL_INTERVAL = 0.1

_cancel_event: contextvars.ContextVar[Optional[threading.Event]] = (
    contextvars.ContextVar("titiler_openeo_cancel_event", default=None)
)


class RequestCancelled(Exception):
    """The client disconnected, so the request work was stopped."""


def raise_if_cancelled() -> None:
    """Raise ``RequestCancelled`` if the current request was cancelled."""
    event = _cancel_event.get()
    if event is not None and event.is_set():
        raise RequestCancelled("client disconnected")


def cancellable_sleep(seconds: float) -> None:
    """Sleep ``seconds``, but wake and raise if the request is cancelled.

    Outside a request, this is ``time.sleep``.
    """
    event = _cancel_event.get()
    if event is None:
        time.sleep(seconds)
    elif event.wait(seconds):
        raise RequestCancelled("client disconnected")


def submit_in_context(
    executor: Executor, fn: Callable[..., T], *args: Any, **kwargs: Any
) -> "Future[T]":
    """Submit ``fn`` to ``executor`` with a copy of the current context.

    ``ThreadPoolExecutor`` does not copy contextvars to its threads, so without
    this the worker threads do not see the cancel flag of the request.
    """
    ctx = contextvars.copy_context()
    return executor.submit(ctx.run, fn, *args, **kwargs)


async def _watch_disconnect(request: Request, event: threading.Event) -> None:
    """Set ``event`` when the client of ``request`` disconnects.

    The event is also set when this task is cancelled: if an enclosing scope
    is cancelled (for example at server shutdown), the worker must stop too,
    because ``run_cancellable`` waits for it.
    """
    try:
        while not await request.is_disconnected():
            await anyio.sleep(DISCONNECT_POLL_INTERVAL)
    finally:
        event.set()


async def run_cancellable(request: Request, fn: Callable[[], T]) -> T:
    """Run the sync ``fn`` in a worker thread, cancelled on client disconnect.

    ``fn`` sees the cancel flag through ``raise_if_cancelled``. The thread is
    never abandoned: this returns only when ``fn`` has stopped, so the thread
    limit of the server stays correct.

    Raises:
        RequestCancelled: the client disconnected and ``fn`` stopped at a
            checkpoint.
    """
    event = threading.Event()

    def run() -> T:
        _cancel_event.set(event)
        # The client can go away while this waits for a free worker thread.
        raise_if_cancelled()
        return fn()

    result: Optional[T] = None
    error: Optional[BaseException] = None
    async with anyio.create_task_group() as tg:
        tg.start_soon(_watch_disconnect, request, event)
        # `fn` runs in this task, not as a child task, and its exception is
        # raised after the task group: anyio wraps exceptions of child tasks in
        # an ExceptionGroup, which the FastAPI exception handlers do not match.
        try:
            result = await anyio.to_thread.run_sync(run)
        except Exception as exc:
            error = exc
        tg.cancel_scope.cancel()

    # If an enclosing scope was cancelled, raise that cancellation, not the
    # error of the stopped work.
    await anyio.lowlevel.checkpoint_if_cancelled()
    if error is not None:
        raise error
    return result  # type: ignore[return-value]
