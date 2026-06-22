# Audit: asynchronousness & concurrency isolation in titiler-openeo

**Scope:** static analysis only — no runtime behavior changed. Findings cited as
`file:line`. Follow-up to the `/healthz` starvation incident (PR #303).

---

## 0. Executive summary

- The app is **almost entirely sync**. Only `GET /healthz`
  ([health.py:114](../../titiler/openeo/health.py#L114)) and the cache
  middleware ([middleware.py:58](../../titiler/openeo/middleware.py#L58)) are
  `async def`. Every other route is `def`, so it runs in Starlette's shared
  anyio threadpool (**default 40 tokens, never resized** — there is no `anyio`
  import anywhere in `titiler/`).
- **`GET /readyz` is still sync** ([health.py:126](../../titiler/openeo/health.py#L126)).
  It is the same class of bug PR #303 just fixed for `/healthz`: under compute
  saturation the readiness probe cannot get an anyio thread to even start, so it
  times out and the pod is pulled from the Service — *reducing* capacity exactly
  when load is highest. **This is the top finding.**
- There is **no blocking-on-the-event-loop bug**: all blocking I/O (`requests`,
  SQLAlchemy, duckdb, rasterio/GDAL, `pystac_client`) lives in sync handlers, so
  it runs in the threadpool, not on the loop. The two `async def` paths do no
  blocking work.
- **Severe thread oversubscription** on the compute path: a single sync request
  thread fans out to a rio-tiler pool of `MAX_THREADS` (default `cpu_count*5`,
  measured **110** on a 22-core box), each read then using `GDAL_NUM_THREADS`.
  With the 40-token anyio pool that is up to `40 × 110 × GDAL_NUM_THREADS`
  GDAL-reading threads **per worker process**, and there are `WEB_CONCURRENCY`
  processes. Nothing bounds this; there is no backpressure (no 429/503).
- **Thread budget is configured in three uncoordinated places** under two
  different env vars and an implicit default. See §6.

---

## 1. Endpoint inventory

All routes in [factory.py](../../titiler/openeo/factory.py) are registered as
plain `def` (sync ⇒ anyio threadpool). Health routes in
[health.py](../../titiler/openeo/health.py).

| Route | Handler / line | Decl | Blocking work | Runs on |
|---|---|---|---|---|
| `GET /healthz` | `healthz` [health.py:114](../../titiler/openeo/health.py#L114) | **async** | none | **event loop** ✅ |
| `GET /readyz` | `readyz` [health.py:126](../../titiler/openeo/health.py#L126) | sync | dispatches checks to a dedicated 4-worker pool, blocks on `future.result(timeout)` [health.py:49-50](../../titiler/openeo/health.py#L49) | anyio pool ⚠️ |
| `GET /` | `openeo_root` [factory.py:157](../../titiler/openeo/factory.py#L157) | sync | none (in-memory) | anyio pool |
| `GET /file_formats` | [factory.py:216](../../titiler/openeo/factory.py#L216) | sync | none | anyio pool |
| `GET /credentials/oidc` | [factory.py:290](../../titiler/openeo/factory.py#L290) | sync | none | anyio pool |
| `GET /credentials/basic` | [factory.py:341](../../titiler/openeo/factory.py#L341) | sync | auth (in-memory basic) | anyio pool |
| `GET /me` | [factory.py:369](../../titiler/openeo/factory.py#L369) | sync | auth validate | anyio pool |
| `GET /.well-known/openeo` | [factory.py:391](../../titiler/openeo/factory.py#L391) | sync | none | anyio pool |
| `GET /processes` | [factory.py:420](../../titiler/openeo/factory.py#L420) | sync | none | anyio pool |
| `GET /collections` | `openeo_collections` [factory.py:445](../../titiler/openeo/factory.py#L445) | sync | **STAC network** (`get_collections`, cached) | anyio pool |
| `GET /collections/{id}` | [factory.py:475](../../titiler/openeo/factory.py#L475) | sync | **STAC network** | anyio pool |
| `GET /conformance` | [factory.py:509](../../titiler/openeo/factory.py#L509) | sync | none | anyio pool |
| `GET /services` | [factory.py:534](../../titiler/openeo/factory.py#L534) | sync | **store DB**, file I/O | anyio pool |
| `GET /services/{id}` | [factory.py:617](../../titiler/openeo/factory.py#L617) | sync | **store DB** | anyio pool |
| `GET /process_graphs` | [factory.py:649](../../titiler/openeo/factory.py#L649) | sync | **store DB** | anyio pool |
| `GET /process_graphs/{id}` | [factory.py:696](../../titiler/openeo/factory.py#L696) | sync | **store DB** | anyio pool |
| `DELETE /process_graphs/{id}` | [factory.py:731](../../titiler/openeo/factory.py#L731) | sync | **store DB** | anyio pool |
| `POST /validation` | [factory.py:754](../../titiler/openeo/factory.py#L754) | sync | CPU (graph parse/validate) | anyio pool |
| `PUT /process_graphs/{id}` | [factory.py:875](../../titiler/openeo/factory.py#L875) | sync | CPU + **store DB** | anyio pool |
| `POST /services` | [factory.py:1008](../../titiler/openeo/factory.py#L1008) | sync | CPU + **store DB** | anyio pool |
| `DELETE /services/{id}` | [factory.py:1085](../../titiler/openeo/factory.py#L1085) | sync | **store DB** | anyio pool |
| `PATCH /services/{id}` | [factory.py:1119](../../titiler/openeo/factory.py#L1119) | sync | **store DB** | anyio pool |
| `GET /service_types` | [factory.py:1171](../../titiler/openeo/factory.py#L1171) | sync | none | anyio pool |
| **`POST /result`** | `openeo_result` [factory.py:1290](../../titiler/openeo/factory.py#L1290) | sync | **heavy: STAC + GDAL reads + numpy** via `pg_callable` [factory.py:1319](../../titiler/openeo/factory.py#L1319) | anyio pool 🔴 |
| **`GET /services/xyz/{id}/tiles/{z}/{x}/{y}`** | `openeo_xyz_service` [factory.py:1351](../../titiler/openeo/factory.py#L1351) | sync | **heavy: STAC + GDAL reads + numpy** via `pg_callable` [factory.py:1467](../../titiler/openeo/factory.py#L1467) | anyio pool 🔴 |

**`async def` calling blocking code directly on the loop:** none found.
`healthz` is a pure no-op return; `DynamicCacheControlMiddleware` only mutates
headers. So finding #4 (blocking-on-loop) is **clean** — *as long as the rule
"new `async def` handlers must not call the sync stores / `requests` /
rasterio / `pystac_client`" is enforced going forward.*

---

## 2. Threadpool topology

Three distinct threadpool layers exist, plus GDAL's internal threads:

### Layer A — anyio request threadpool (per process)

- Starlette runs every sync `def` handler via `anyio.to_thread.run_sync`, which
  uses a process-global `CapacityLimiter` defaulting to **40**.
- **Never resized:** no `anyio`, `to_thread`, `RunVar`, or
  `CapacityLimiter` usage anywhere in `titiler/` (grep clean). So the cap is the
  framework default of 40 concurrent sync handlers per worker.

### Layer B — rio-tiler `create_tasks` pool (per load node)

- [io.py:60-66](../../titiler/openeo/processes/implementations/io.py#L60) (`load_url`)
  and [stacapi.py:1049-1060](../../titiler/openeo/stacapi.py#L1049) (`load_collection`)
  call `rio_tiler.tasks.create_tasks(_reader, items, MAX_THREADS, ...)`.
- `create_tasks` opens `ThreadPoolExecutor(max_workers=MAX_THREADS)` and submits
  one `_reader` per item. **Important:** the `return` is *inside* the `with`
  block, so `executor.__exit__` → `shutdown(wait=True)` runs before it returns.
  ⇒ **the reads execute eagerly and block the calling thread**, parallelized to
  `MAX_THREADS`, and the returned futures are already *done*.
- `MAX_THREADS` = `rio_tiler.constants.MAX_THREADS` =
  `RIO_TILER_MAX_THREADS` or `cpu_count()*5` (**measured 110** here).

### Layer C — RasterStack pool (per stack access)

- [data_model.py:505](../../titiler/openeo/processes/implementations/data_model.py#L505)
  `_execute_selected_tasks` opens **another** `ThreadPoolExecutor(max_workers=self._max_workers)`,
  with `_max_workers` defaulting to the same `MAX_THREADS`
  ([data_model.py:307](../../titiler/openeo/processes/implementations/data_model.py#L307)).
- Driven by `.values()` [data_model.py:611](../../titiler/openeo/processes/implementations/data_model.py#L611),
  `.items()` [data_model.py:622](../../titiler/openeo/processes/implementations/data_model.py#L622),
  `prefetch` [data_model.py:464](../../titiler/openeo/processes/implementations/data_model.py#L464).
- Submitted work is `_execute_task` [data_model.py:466](../../titiler/openeo/processes/implementations/data_model.py#L466),
  which calls `future.result()`.

**Nesting / where the work actually lands (config-dependent):**

- When `MAX_THREADS > 1` (the normal case): Layer B already ran the reads to
  completion, so Layer C's pool just calls `.result()` on done futures — cheap,
  but it still **spins up a second pool of up to 110 idle-ish threads** per
  stack access. Largely redundant.
- When `RIO_TILER_MAX_THREADS=1` (plausible on the CDSE `WEB_CONCURRENCY=1`
  profile): `create_tasks` takes the `else` branch and returns *partials*, so
  the actual GDAL reads happen later inside Layer C's pool instead. The heavy
  work simply moves from Layer B to Layer C — the total still scales with
  `MAX_THREADS`.

### Layer D — GDAL internal threads

- Each `_reader`/rasterio read may use `GDAL_NUM_THREADS` worker threads
  (decompression/warp), set per env.

### Worst-case thread count (per worker process)

```
threads ≈ Layer A (40) × MAX_THREADS (B or C, ~110 default) × GDAL_NUM_THREADS
```

With defaults that is `40 × 110 × N_gdal` GDAL-reading threads in one process,
and there are `WEB_CONCURRENCY` such processes (1 on CDSE, **32 on EOAPI**).
Even with prod caps on `RIO_TILER_MAX_THREADS`/`GDAL_NUM_THREADS`, the
**40× multiplier from the unbounded anyio pool is the structural problem**: the
per-request fan-out is sized for *one* request having the box to itself, but up
to 40 run concurrently. This is classic threadpool oversubscription → context-
switch thrash, memory blow-up (each GDAL read buffers tiles), and the probe
starvation that caused the incident.

> Note: the dedicated `/readyz` executor (Layer-A-sibling, 4 workers,
> [health.py:36](../../titiler/openeo/health.py#L36)) is correctly isolated from
> all of the above — its only flaw is being *reached* via a sync handler (§3).

---

## 3. Critical-path isolation

| Probe / control | Isolated from compute? | Notes |
|---|---|---|
| `GET /healthz` | ✅ now | async no-op on the loop; cannot be starved by the threadpool ([health.py:114](../../titiler/openeo/health.py#L114)). |
| `GET /readyz` | ❌ **no** | sync handler ⇒ needs a Layer-A token to start. Under compute saturation all 40 tokens are held by `pg_callable`, so `readyz` queues and the probe times out. Its dedicated 4-worker executor doesn't help because the handler can't even begin. |
| cheap GETs (`/`, `/processes`, `/conformance`, `/me`, …) | ❌ no | all share the 40-token pool with `/result` and tiles; cheap control calls queue behind heavy compute. |

**Recommendation:** compute (`/result`, XYZ tiles) must run on a **bounded,
dedicated executor** so it can never consume the tokens that probes and cheap
control endpoints need. The event loop + probes stay responsive; cheap sync
endpoints keep the general anyio pool to themselves. See §5/R2–R3.

---

## 4. Blocking-on-loop bugs

**None present today.** Blocking clients are all confined to sync handlers
(threadpool), not the loop:

- `requests` session GET in `stacApiBackend.ping` [stacapi.py:81](../../titiler/openeo/stacapi.py#L81) and `client.search`/`get_collections` (sync).
- SQLAlchemy sync engine — `ping` [sqlalchemy.py:119](../../titiler/openeo/services/sqlalchemy.py#L119), every store method uses `with Session(...)`.
- duckdb sync connections — `ping` [duckdb.py:48](../../titiler/openeo/services/duckdb.py#L48).
- rasterio/GDAL + numpy in `pg_callable`.

**Guardrail (so it stays clean):** the moment any of these is called from an
`async def`, it blocks the whole event loop (and the probes with it). Add a
lint/review rule. The riskiest near-term temptation is making `/readyz` async by
calling `*.ping()` directly — that would reintroduce the bug. Do it by awaiting
the **executor** (R4), not by calling the sync clients inline.

---

## 5. Recommendations (prioritized, with diff-level guidance)

### R1 — Size the anyio threadpool from config *(highest leverage, smallest change)*

Today it is the implicit default of 40 and decoupled from `MAX_THREADS`, so the
two multiply blindly. Set the limiter once at startup from an env-driven
setting. Add to `ApiSettings` ([settings.py:131](../../titiler/openeo/settings.py#L131)):

```python
max_threadpool_workers: int = 40   # TITILER_OPENEO_API_MAX_THREADPOOL_WORKERS
```

and in `create_app()` ([main.py:52](../../titiler/openeo/main.py#L52)) add a startup hook:

```python
import anyio
@app.on_event("startup")
async def _tune_threadpool() -> None:
    limiter = anyio.to_thread.current_default_thread_limiter()
    limiter.total_tokens = api_settings.max_threadpool_workers
```

Tune so that `max_threadpool_workers × MAX_THREADS × GDAL_NUM_THREADS` fits the
pod's CPU/memory budget rather than being left at `40 × 110 × N`.

### R2 — Dedicated bounded executor for compute + backpressure *(fixes root cause)*

Give `/result` and the XYZ tile handler their own bounded pool so they can never
starve probes or cheap endpoints, and **shed load** instead of queueing
unboundedly. Sketch (module-level, sized from settings):

```python
_COMPUTE = concurrent.futures.ThreadPoolExecutor(max_workers=settings.compute_workers,
                                                  thread_name_prefix="compute")
_COMPUTE_SLOTS = threading.Semaphore(settings.compute_workers + settings.compute_queue)

# in the handler:
if not _COMPUTE_SLOTS.acquire(blocking=False):
    raise HTTPException(503, "server busy", headers={"Retry-After": "1"})
try:
    fut = _COMPUTE.submit(pg_callable, named_parameters=parameters)
    img = fut.result()
finally:
    _COMPUTE_SLOTS.release()
```

Pairs naturally with R5 (make these handlers `async def` and `await` the future
via `anyio.to_thread`/`loop.run_in_executor`, so the loop stays free and the 503
is returned promptly). Return **429/503 + `Retry-After`** rather than letting
work pile up. This is the structural fix for the incident.

### R3 — Collapse the redundant RasterStack pool (Layer C)

Because `create_tasks` (Layer B) already runs reads to completion under
`shutdown(wait=True)`, `_execute_selected_tasks`
([data_model.py:505](../../titiler/openeo/processes/implementations/data_model.py#L505))
usually spins up a second 110-worker pool only to call `.result()` on *done*
futures. When the futures are already done, iterate them directly (or use
`rio_tiler.tasks.filter_tasks`) instead of opening a new `ThreadPoolExecutor`.
Only fall back to a pool when tasks are still callables (the
`RIO_TILER_MAX_THREADS=1` path). Removes one whole oversubscription layer in the
common case.

### R4 — Make `/readyz` async, awaiting its dedicated executor

Keep the bounded 4-worker check executor, but stop gating it behind a sync
handler. Run the checks off the loop and `await` them so readiness stays
responsive under compute load:

```python
@router.get("/readyz")
async def readyz() -> JSONResponse:
    checks = _build_checks(...)
    results = await anyio.to_thread.run_sync(_run_all_checks, checks)  # uses _executor internally
    ...
```

or wrap each `_run_check` with `loop.run_in_executor(_executor, ...)` and
`asyncio.gather`. The dedicated executor ([health.py:36](../../titiler/openeo/health.py#L36))
already isolates the actual `ping` work; this just removes the Layer-A
dependency so a saturated compute pool can't fail readiness. **Do not** call the
sync `*.ping()` methods directly in the async handler (that reintroduces §4).

### R5 — Which handlers async vs sync

- **Make async** (then offload heavy/blocking work explicitly): `/result`,
  `/services/xyz/.../tiles/...` (await the compute executor, R2); `/readyz`
  (R4). This frees their Layer-A tokens and lets backpressure return immediately.
- **Keep sync-in-threadpool** (cheap or light DB/STAC, fine on the general
  pool): everything else in §1. Don't bulk-convert them to async — they'd then
  need their blocking store/STAC calls offloaded anyway, with no benefit.

### R6 — Probe & HPA guidance

- **Liveness (`/healthz`)**: lenient — generous `timeoutSeconds`,
  `failureThreshold ≥ 3`. It is now an async no-op, so it should only fail on a
  truly wedged loop. Avoid tight timeouts that re-trigger the very restart storm
  PR #303 fixed.
- **Readiness (`/readyz`)**: this is the **load-shedding** signal. After R4 it
  fails only when a dependency is actually unhealthy (not merely when compute is
  busy). If you *want* readiness to shed load under saturation, drive it from the
  R2 compute-queue depth, not from anyio starvation.
- **HPA**: scale on a compute-saturation signal (queue depth / inflight compute
  from R2, or CPU), not on probe flapping.

### R7 — Async-native libs (later, larger)

- `pystac_client` search/`get_collections` and `requests` in `ping` are sync
  network I/O ([stacapi.py:81](../../titiler/openeo/stacapi.py#L81),
  [stacapi.py:259](../../titiler/openeo/stacapi.py#L259)). They're tolerable in
  the threadpool today. If `/collections*` latency or token pressure becomes an
  issue, move STAC metadata calls to `httpx.AsyncClient` and make those handlers
  async. **Leave GDAL/rasterio reads in a (bounded) pool** — they're the right
  fit for threads since GDAL releases the GIL; a **process pool** is worth
  considering only for the heavy numpy reductions if profiling shows GIL
  contention (CPU-bound under the GIL gets no parallelism from threads, whereas
  GDAL/numpy that release the GIL do).

---

## 6. Unify the thread-budget configuration *(requested)*

Today the thread budget is scattered and inconsistent:

| Knob | Where | Source | Default | Scope |
| --- | --- | --- | --- | --- |
| `WEB_CONCURRENCY` (uvicorn `--workers`) | deploy | env | varies (1 CDSE, 32 EOAPI) | **per pod — outer multiplier on every row below** |
| anyio request pool | framework | *(unset — implicit)* | 40 | per process |
| `create_tasks` workers | [io.py:63](../../titiler/openeo/processes/implementations/io.py#L63), [stacapi.py:1052](../../titiler/openeo/stacapi.py#L1052) | `RIO_TILER_MAX_THREADS` env (via `rio_tiler.constants.MAX_THREADS`) | `cpu_count*5` (≈110) | per process |
| `RasterStack._max_workers` | [data_model.py:307](../../titiler/openeo/processes/implementations/data_model.py#L307) | same `MAX_THREADS` import | ≈110 | per process |
| GDAL worker threads | rasterio | `GDAL_NUM_THREADS` env | env-set | per process |
| `/readyz` checks | [health.py:37](../../titiler/openeo/health.py#L37) | hard-coded | 4 | per process |

**Everything except `WEB_CONCURRENCY` is per *process*.** Each `--workers`
process is a separate interpreter with its own GIL, event loop, and a private
copy of every pool above (plus the `stac_client`, the SQLAlchemy engine, the
proposed `_COMPUTE` pool, and the per-process `GDAL_CACHEMAX` memory). So
`WEB_CONCURRENCY` multiplies all of them, and the figure that actually has to
fit the pod is:

```
peak GDAL threads / pod = WEB_CONCURRENCY × anyio(40) × MAX_THREADS(~110) × GDAL_NUM_THREADS
```

Processes buy real CPU parallelism (separate GILs); the per-process pools only
buy concurrency for GIL-releasing work (GDAL/numpy) and I/O. The two layers
**compound**, they don't substitute.

Problems: two different env namespaces (`RIO_TILER_*` / `GDAL_*` vs
`TITILER_OPENEO_*`), one knob (`MAX_THREADS`) reused for two semantically
different pools, one pool with no knob at all (anyio), a magic literal (4), and
`WEB_CONCURRENCY` sitting outside all of it. Nothing expresses the **pod-wide
product** that actually matters.

**Two footguns specific to the `WEB_CONCURRENCY` interaction:**

- **`cpu_count()` double-dipping.** `MAX_THREADS = cpu_count()*5`, and common
  base images also default `WEB_CONCURRENCY` to `cpu_count()`. Inside a container
  `cpu_count()` reports the *node's* cores, not the pod's cgroup CPU *limit* — so
  both the process count *and* the per-process fan-out auto-size off an inflated
  number and then multiply. Pin both explicitly; don't let either auto-derive
  from `os.cpu_count()`.
- **Fork timing (`--preload`).** gunicorn `--preload` forks *after* import, so
  `ThreadPoolExecutor`s, DB engines, and locks created at module load
  ([health.py:36](../../titiler/openeo/health.py#L36), the proposed `_COMPUTE`,
  the SQLAlchemy engine) are duplicated/unsafe across the fork. uvicorn
  `--workers` re-imports per worker (safer). Create executors and connection
  pools lazily / post-fork, not at import.

**Proposal — one `ConcurrencySettings` block, all under `TITILER_OPENEO_`:**

```python
class ConcurrencySettings(BaseSettings):
    # Layer A: concurrent sync HTTP handlers per worker process
    request_workers: int = 40
    # Layer B/C: per-request read fan-out (maps onto rio-tiler MAX_THREADS)
    read_workers: int = Field(default_factory=lambda: min(8, os.cpu_count() or 4))
    # Layer B/C nested: GDAL threads per read
    gdal_threads: int = 4
    # dedicated compute pool + queue (R2)
    compute_workers: int = 8
    compute_queue: int = 16
    # /readyz check pool
    readyz_workers: int = 4

    model_config = SettingsConfigDict(env_prefix="TITILER_OPENEO_CONCURRENCY_", ...)

    @model_validator(mode="after")
    def _warn_oversubscription(self):
        # The budget that must fit the pod is POD-WIDE: WEB_CONCURRENCY processes,
        # each running `request_workers` concurrent handlers, each fanning out to
        # `read_workers` reads, each using `gdal_threads`.
        web_concurrency = int(os.environ.get("WEB_CONCURRENCY", 1))
        peak_pod = (
            web_concurrency
            * self.request_workers
            * self.read_workers
            * self.gdal_threads
        )
        # Compare against the cgroup CPU *limit*, not os.cpu_count() (which reports
        # the node's cores inside a container). Fall back to cpu_count if unknown.
        cores = _cgroup_cpu_quota() or os.cpu_count() or 1
        if peak_pod > 4 * cores:
            logger.warning(
                "peak GDAL threads/pod = %d (WEB_CONCURRENCY=%d) > 4x cores (%d)",
                peak_pod, web_concurrency, cores,
            )
        return self
```

`_cgroup_cpu_quota()` reads `cpu.max` (cgroup v2) or
`cpu.cfs_quota_us`/`cpu.cfs_period_us` (v1) so the guard reflects the pod's real
CPU allotment. The point is to validate the **pod-wide** product — the same
`request_workers × read_workers × gdal_threads` is harmless at
`WEB_CONCURRENCY=1` (CDSE) and pure thrash at `WEB_CONCURRENCY=32` (EOAPI), and
only a `WEB_CONCURRENCY`-aware check catches that.
Then at startup ([main.py:52](../../titiler/openeo/main.py#L52)) make this the
**single source of truth** and push the values down:

- set the anyio limiter to `request_workers` (R1);
- `os.environ.setdefault("RIO_TILER_MAX_THREADS", str(read_workers))` and
  `GDAL_NUM_THREADS` from `gdal_threads` **before** rasterio/rio-tiler import, so
  `rio_tiler.constants.MAX_THREADS` resolves to `read_workers`;
- pass `read_workers` explicitly into `RasterStack(..., max_workers=...)` and the
  `create_tasks(...)` calls rather than importing `MAX_THREADS` in three modules;
- build the `/readyz` and compute executors from `readyz_workers` /
  `compute_workers`.

Sizing guidance to document alongside it: set `WEB_CONCURRENCY` ≈ the pod's
allocated CPU cores (that's where real parallelism comes from), then keep
`WEB_CONCURRENCY × request_workers × read_workers × gdal_threads` within a small
multiple of cores. At `WEB_CONCURRENCY=1` (CDSE) there is no process-level CPU
parallelism, so CPU-bound numpy serializes under one GIL — that profile is the
case for the process pool in R7.

Benefits: one namespace, names that say *what each pool is for*, the **pod-wide**
oversubscription product (including `WEB_CONCURRENCY`) is computed and validated
in one place against the cgroup CPU limit, and the per-env tuning (CDSE 1-worker
vs EOAPI 32-worker) becomes a single coherent block instead of several env vars
that interact invisibly.

---

## 7. Ranked TODO (issue/PR-sized)

1. **`/readyz` async** — await the existing 4-worker check executor; don't call
   `*.ping()` on the loop. *(small, directly closes the same gap as PR #303)*
2. **Bounded compute executor + 429/503 backpressure** for `/result` and XYZ
   tiles; make those two handlers `async` and `await` it. *(medium, root-cause)*
3. **Size the anyio limiter from config** at startup (R1). *(small)*
4. **Unify thread config** into `ConcurrencySettings` with an oversubscription
   guard, and stop importing `MAX_THREADS` in 3 modules (§6). *(medium)*
5. **Drop the redundant RasterStack pool** when `create_tasks` already returned
   done futures (R3). *(small–medium)*
6. **Probe/HPA tuning**: lenient liveness, readiness/queue-depth-driven shedding,
   HPA on compute saturation (R6). *(ops/Helm)*
7. **Lint/review rule**: no blocking client (`requests`, SQLAlchemy, duckdb,
   rasterio, `pystac_client`, `.result()`, `time.sleep`) inside any `async def`.
   *(small, prevents regression of §4)*
8. **Maintained production-sizing doc** (`docs/src/operations/sizing.md`) — a
   living ops reference, kept in step with `ConcurrencySettings` (#4), covering:
   - the layered concurrency model and the **pod-wide product**
     `WEB_CONCURRENCY × request_workers × read_workers × gdal_threads × …` (§2, §6);
   - the **rules of thumb**: `WEB_CONCURRENCY` ≈ allocated cores; keep the
     pod-wide product within a small multiple of cores; size pools per-process
     and remember `WEB_CONCURRENCY` multiplies them;
   - the **footguns**: `os.cpu_count()` reports node cores not the cgroup limit;
     `--preload` fork timing; per-process `GDAL_CACHEMAX` memory;
   - **per-profile worked examples** (CDSE `WEB_CONCURRENCY=1` vs EOAPI `=32`)
     with concrete recommended env values and the resulting thread/memory budget;
   - probe/HPA guidance (cross-link §3, R6) and how the oversubscription
     validator warning maps back to which knob to turn down.

   **Mechanism — single-source the knob reference from the code** (the existing
   mkdocstrings setup, [docs/mkdocs.yml:70](../../docs/mkdocs.yml#L70)):
   - write the canonical knob docs as a Google-style **class docstring** on
     `ConcurrencySettings` (carry the rule-of-thumb formula here so it renders
     too) plus a **string-literal attribute docstring** under each field —
     griffe renders those more reliably than `Field(description=...)`;
   - in the prose page, inject the live reference so names/types/defaults are
     generated, not hand-maintained:

     ```markdown
     ::: titiler.openeo.settings.ConcurrencySettings
         options:
           show_root_heading: true
           members_order: source
     ```

   - fix the python handler `paths` so griffe resolves the repo-root package:
     `paths: [.., src]` ([docs/mkdocs.yml:74](../../docs/mkdocs.yml#L74));
   - add an `Operations` nav entry pointing at the page
     ([docs/mkdocs.yml:33](../../docs/mkdocs.yml#L33)).

   This makes the defaults table un-driftable (rendered from source); only the
   prose examples remain hand-written, with `_warn_oversubscription` as the
   runtime backstop. Add a CI/PR-review checkbox so the prose and
   `ConcurrencySettings` stay in sync. *(small–medium, ops; depends on #3/#4)*
9. **(Later)** evaluate async STAC/`httpx` for `/collections*` and a process pool
   for numpy-heavy reductions if profiling justifies it (R7). *(large)*
