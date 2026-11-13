# Memory tuning & native caches

titiler-openeo uses memory in two very different places, and an OOM kill looks
identical for both:

- **Python heap** — the `ImageData`/numpy cubes produced while evaluating a
  process graph.
- **Native / off-heap** — GDAL and `/vsi*` caches. These live **outside** the
  Python heap, are **not** freed by the garbage collector, and add *on top* of
  per-request working memory. This page is about keeping them bounded.

> The single most common production footgun is shipping a **dev** `VSI_CACHE_SIZE`
> (512 MB) to Kubernetes. It is a *per-file-handle* cache and will OOM a pod.

## The three native knobs

| Variable | Scope | Default | Notes |
|----------|-------|---------|-------|
| `GDAL_CACHEMAX` | **process-global** block cache | 5% of RAM | Values `<100000` are **MB**, otherwise **bytes**. One cache per worker process, so it multiplies by `WEB_CONCURRENCY`. |
| `VSI_CACHE_SIZE` | **per file-handle** | 25 MB | In-memory cache of each open file. Total ≈ `VSI_CACHE_SIZE × (concurrently open handles)`. **This is the dangerous one.** |
| `CPL_VSIL_CURL_CACHE_SIZE` | **process-global** LRU for `/vsicurl` chunk reads | 16 MB | Pin it so it can't drift; raise only if the budget allows. |

`VSI_CACHE` must be `TRUE` for `VSI_CACHE_SIZE` to apply.

### Why `VSI_CACHE_SIZE` is the trap

It is allocated **once per open file handle**, not once per process. A single
process-graph request fans out to roughly `items × bands × read_threads` open
handles (the `ThreadPoolExecutor` reads in `RasterStack` plus rio-tiler's mosaic
reader). At the dev value of 512 MB per handle, even a few dozen concurrent
handles blow past a 2 Gi container; at the chart default of 5 MB the same fan-out
costs a few hundred MB at most.

## The native budget

Per pod, the native caches cost roughly:

```
native ≈ WEB_CONCURRENCY × (GDAL_CACHEMAX + CPL_VSIL_CURL_CACHE_SIZE)   # process-global
       + (concurrently open file handles) × VSI_CACHE_SIZE             # per-handle
```

This must sit comfortably **below** `resources.limits.memory`, leaving the
remainder for the Python heap (the process-graph cubes). Worked example for the
chart defaults at a 2 Gi limit, single worker:

```
GDAL_CACHEMAX            = 200 MB   (process-global)
CPL_VSIL_CURL_CACHE_SIZE =  64 MB   (process-global)
VSI_CACHE_SIZE           =   5 MB × ~100 handles ≈ 500 MB (worst case)
--------------------------------------------------------------
native peak              ≈ 0.2 + 0.06 + 0.5 ≈ 0.76 GB
remaining for heap       ≈ 2 Gi − 0.76 ≈ 1.2 GB
```

If you raise `WEB_CONCURRENCY` (multiple uvicorn workers per pod), remember the
**process-global** caches multiply by it — `GDAL_CACHEMAX × WEB_CONCURRENCY`
alone can dominate the limit.

## Recommended production values (Helm chart defaults)

The chart (`deployment/k8s/charts/values.yaml`) ships safe defaults:

```yaml
env:
  GDAL_CACHEMAX: "200"                # 200 MB, process-global
  VSI_CACHE: "TRUE"
  VSI_CACHE_SIZE: "5000000"           # 5 MB PER HANDLE
  CPL_VSIL_CURL_CACHE_SIZE: "67108864"  # 64 MB, pinned
```

Rules of thumb:

- Keep `VSI_CACHE_SIZE` in the low single-digit MB. Never copy a dev value here.
- Keep `GDAL_CACHEMAX + CPL_VSIL_CURL_CACHE_SIZE` (× `WEB_CONCURRENCY`) to a small
  fraction of the limit.
- Size `WEB_CONCURRENCY` to the pod's CPU, then re-check this budget.

## Dev profiles are not production

`.env.cdse` / `.env.eoapi` are local-developer profiles (single user, low
concurrency) and intentionally use a large `VSI_CACHE_SIZE` (512 MB) and
`CPL_VSIL_CURL_CACHE_SIZE` (200 MB) for throughput. **Do not** copy these into a
Helm release or container deployment — they are flagged inline in those files.

## Guard

A Helm unit test (`deployment/k8s/charts/tests/native_cache_test.yaml`) asserts
the chart's native-cache env vars stay at their bounded defaults, so an
accidental bump (e.g. pasting a dev `VSI_CACHE_SIZE`) fails CI and forces a
conscious review.
