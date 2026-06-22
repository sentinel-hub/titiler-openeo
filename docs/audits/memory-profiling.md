# Memory profiling harness

How to measure where a process graph spends memory, so the fixes in EPIC #305
(see [`memory-oom-audit.md`](memory-oom-audit.md)) can be confirmed with numbers
instead of guesses. This is **subtask 1** of that EPIC.

The harness separates the two things that look identical to the OOM killer:

- **Python heap** — the retained `ImageData`/numpy cubes (`tracemalloc`).
- **Native / off-heap** — GDAL block cache, VSI/curl caches (`RSS − heap`).

> ⚠️ Debug only. `tracemalloc` roughly doubles allocation cost and the harness
> is process-global / not concurrency-safe. Never enable it in production.

## 1. In-process harness (per-node + retention)

Enable with one env var; everything is a no-op otherwise.

| Env var | Default | Effect |
|---------|---------|--------|
| `TITILER_OPENEO_PROFILING_MEMORY` | `false` | per-node heap deltas, graph heap/RSS summary, end-of-graph retention |
| `TITILER_OPENEO_PROFILING_MEMORY_BACKREFS` | `false` | also dump an `objgraph` back-ref graph for a sample retained array (needs `objgraph`) |
| `TITILER_OPENEO_PROFILING_MEMORY_BACKREFS_DIR` | `/tmp` | where the back-ref image is written |

Run a single graph through the standalone runner (no uvicorn/threads in the way):

```bash
TITILER_OPENEO_PROFILING_MEMORY=true \
TITILER_OPENEO_STAC_API_URL=https://stac.eoapi.dev \
uv run python scripts/profile_memory.py path/to/graph.json
```

`graph.json` is the same body `POST /result` accepts (a full process definition
or a bare process graph). You get log lines like:

```
[mem] profile graph.json START heap=12.3MB rss=180.0MB
[mem] node=load_collection depth=0 heap_delta=+629.0MB rss_delta=+940.0MB heap_now=641.3MB rss_now=1.1GB native~=470.0MB
[mem]   node=ndvi depth=1 heap_delta=+900.0MB ...
[mem] node=aggregate_temporal depth=0 heap_delta=+27.0MB ...
[mem] profile graph.json END heap=1.5GB (peak=1.7GB) rss=1.9GB native~=400.0MB
[mem] graph.json: nodes_pinned=5 retained_arrays=204 retained_bytes=1.5GB
```

Reading it:

- **`heap_delta`** — net bytes a node *kept* (what the graph engine now pins).
  `load_collection` and `ndvi` both staying large confirms the audit's "two full
  cubes alive at once".
- **`depth`** — `0` = top-level graph node; `>0` = nested reducer call
  (e.g. `aggregate_temporal` → `mean` → `apply_pixel_selection`).
- **`native~=` / END `native~=`** — `RSS − heap`, the GDAL/VSI off-heap estimate.
  Large here ⇒ tune `GDAL_CACHEMAX`/`VSI_CACHE_SIZE` (EPIC subtask 6); large
  `heap` ⇒ the retention/dtype fixes (subtasks 2–5).
- **retention line** — `nodes_pinned` is `len(results_cache)`;
  `retained_bytes` sums unique array buffers (shared `ImageData` counted once).

The same lines are emitted in-app for `POST /result` and the XYZ tile endpoint
when the env var is set, so you can profile against a running dev server too.

## 2. memray (full allocation flamegraph, heap + native)

For an allocation-site flamegraph including native allocations:

```bash
TITILER_OPENEO_STAC_API_URL=https://stac.eoapi.dev \
uv run memray run -o /tmp/openeo.bin scripts/profile_memory.py path/to/graph.json
uv run memray flamegraph /tmp/openeo.bin     # -> /tmp/openeo-flamegraph.html
# peak-memory view:
uv run memray flamegraph --temporal --leaks /tmp/openeo.bin
```

`memray` is not a project dependency; install it ad-hoc (`uv pip install memray`)
or add it to the dev group when doing a profiling session.

## 3. The reference graph (issue #300)

Use a `load_collection` over a multi-year extent → `ndvi` → `aggregate_temporal`
(a few intervals) → `reduce_dimension('t')` → `save_result(GTiff)`. Watch for:

1. `load_collection` and `ndvi` heap_delta both staying resident through
   `aggregate_temporal` (retention; subtasks 4–5).
2. `ndvi` heap_delta being *larger* than the source it consumed (float64 upcast;
   subtask 2).
3. END `peak` ≈ Σ of stages rather than the working set.
4. `native~=` vs `heap` ratio (which fix family applies).

## 4. Heap vs native, quickly

- `heap_now` close to `rss_now` → almost everything is Python heap → focus on
  retention + dtype (subtasks 2–5).
- Large `native~=` (RSS ≫ heap) → off-heap GDAL/VSI caches dominate → focus on
  cache sizing (subtask 6). Cross-check with `rasterio`/GDAL:
  `from osgeo import gdal; gdal.GetCacheUsed(), gdal.GetCacheMax()`.
