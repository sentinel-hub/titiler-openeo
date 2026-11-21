# ADR 0011 — Cooperative cancellation of tile requests

- **Status:** Accepted
- **Date:** 2026-10-20
- **Deciders:** @emmanuelmathot
- **Related:** [#471](https://github.com/sentinel-hub/titiler-openeo/pull/471), [ADR 0001](0001-sar-backscatter.md), [ADR 0002](0002-band-sources.md), [ADR 0005](0005-asset-href-signing.md)

---

## 1. Context

A map viewer requests many XYZ tiles. When the user pans or zooms, the viewer
aborts the tiles that are not visible anymore. Before this ADR, the server did
not see the abort. The tile endpoint was a sync handler. It ran in a worker
thread until the end: STAC search, all COG reads, PNG encoding. Then it sent
the response to a closed socket.

During fast panning, these abandoned tiles used the worker threads (40 for
each process by default), the memory and the bandwidth to the storage. The
visible tiles had to wait for them.

Python cannot stop a thread from outside. The only possible solution is
cooperative: the work must check a flag at safe points and stop itself.

## 2. Decision

### 2.1 Detection

The XYZ route is `async`. It calls `run_cancellable(request, fn)`
([`cancellation.py`](../../titiler/openeo/cancellation.py)), which:

1. Runs the sync tile render `fn` in a worker thread
   (`anyio.to_thread.run_sync`). The thread is never abandoned: the call
   returns only when `fn` has stopped, so the thread limit stays correct.
2. Waits for `http.disconnect` on the ASGI `receive` channel in a watcher
   task. On disconnect, or when an enclosing scope is cancelled, the watcher
   sets a `threading.Event`.

The watcher is the pattern of Starlette's
`StreamingResponse.listen_for_disconnect`. Uvicorn's `receive()` waits on an
event that it sets when the connection closes, so the watcher costs nothing
while it waits and sees the disconnect at once. We did not poll
`request.is_disconnected()`: polling adds a delay and wakes the event loop
for nothing.

The watcher reads the ASGI messages. Thus, `run_cancellable` must run after
the request body was read, or it takes the body chunks that were not read.
A GET has no body, and FastAPI reads the body before the endpoint runs.

When the flag stops the work, the route returns `499` with
`Cache-Control: no-store`. Nobody reads this response.

### 2.2 Channel: a ContextVar

The flag is a `threading.Event` in a `ContextVar`. The read path calls
`raise_if_cancelled()` at these checkpoints:

- At the start of each attempt in `_reader` (one item read). The delay
  between retries is `cancellable_sleep()`, which stops when the flag is set.
- In `RasterStack._execute_task` (one stack slice).
- In `load_collection`, after the STAC search.

ADR 0001 (§7.10(b)), ADR 0002 (§3) and ADR 0005 (§2.2) rejected a ContextVar
as a channel, because a `ThreadPoolExecutor` does not copy the context to its
threads. We use one here for these reasons:

- **The failure is safe.** For the reader requirements or for href signing, a
  lost value gives wrong data or a failed read. For cancellation, a lost flag
  only means that the work is not cancelled. That is the behavior before this
  ADR.
- **We control the pools that matter.** `RasterStack` submits its tasks with
  `submit_in_context()`, which copies the context. `load_stac` and `load_url`
  now create lazy tasks (`create_tasks(..., threads=0)`), so their reads run in
  the `RasterStack` pool and not in a rio-tiler pool. `load_collection` already
  did this. Thus, `_reader` runs with the flag on all load paths.
- **The other channels do not fit.** The item is ingest-scoped and stays in the
  results cache, so it cannot carry a per-request flag. A parameter in each
  process function would change every process signature.

### 2.3 Rule for new code

A new thread pool in the read path must submit with `submit_in_context()`.
A new long operation (a loop over items, a wait, a retry) should call
`raise_if_cancelled()` or `cancellable_sleep()`. Outside a request (CLI,
scripts, tests), there is no flag and the two functions do nothing more
than the normal behavior.

## 3. Consequences

- An abandoned tile stops at its next checkpoint. It does not start the reads
  that are in the queue (`executor.shutdown(cancel_futures=True)`).
- **Limit:** a GDAL HTTP range read that has started cannot be stopped. The
  smallest unit that stops is one item read.
- **Limit:** the reverse proxy must close the upstream connection when the
  client aborts. For nginx, `proxy_ignore_client_abort` must stay `off` (the
  default).
- `load_stac` and `load_url` read when the stack is read, not at load time.
  Before, `create_tasks(..., MAX_THREADS)` read all items before it returned,
  and each `Future` kept its `ImageData`. `RasterStack.release()` does not
  remove the tasks, so the reference-counted eviction
  ([memory-management](../src/memory-management.md)) could not free the source
  arrays of these two loaders. Lazy tasks keep only their arguments, so the
  eviction now works for them as it does for `load_collection`.
- A process that reads a `load_stac` stack one slice at a time, without
  prefetch, now reads the items one after the other. Before, they were read in
  parallel at load time.
- `/result` does not use `run_cancellable` yet. It can use the same wrapper.
