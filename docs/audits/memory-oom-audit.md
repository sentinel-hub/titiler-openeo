# Audit: memory usage, retention & OOM in titiler-openeo

**Scope:** static analysis of `main` (commit `e5586b2`) for the cause(s) of production
OOM kills. The driving hypothesis — that peak memory ≈ the **sum of all intermediate
cubes** rather than the working set, because the graph topology and the
RasterStack/ImageRef caches keep every realized array referenced — is **confirmed**,
and is compounded by (a) a second retention path inside the graph library, (b) an
NDVI dtype upcast to float64, and (c) a per-request pixel budget (`MAX_PIXELS`) that
is set far above what the 2 Gi container can hold.

No runtime behavior was changed. Illustrative diffs are marked “illustrative — not
applied”.

---

## 0. TL;DR

| # | Finding | Heap/Native | Peak/Sustained | Severity |
|---|---------|-------------|----------------|----------|
| 1 | Graph `results_cache` retains **every node's** return value for the whole eval; never evicted | Heap | Sustained | **Critical** |
| 2 | Same result stored a **second** time in `self.workflow` (`_info`) for ImageData/RasterStack nodes | Heap | Sustained | High |
| 3 | `RasterStack._data_cache` is write-only — once a slice is realized it lives for the stack's lifetime; `filter_keys`/`from_images` share, never evict | Heap | Sustained | **Critical** |
| 4 | NDVI/`normalized_difference` upcasts uint16 → **float64** (4× source width) and risks uint16 wrap before the divide | Heap | Sustained | High |
| 5 | `MAX_PIXELS = 1.5e9` budget ignores #cubes, masks and dtype; allows a working set many× the 2 Gi limit | n/a (policy) | — | **Critical** |
| 6 | Copy spikes: `numpy.stack`+`moveaxis` (spectral), `numpy.ma.concatenate` (GTiff), masked-array `astype`/`filled` | Heap | Peak | Medium |
| 7 | `VSI_CACHE_SIZE` is **per file-handle**; multiplies by concurrent open handles (items × bands × threads) | Native | Peak | Medium |
| 8 | No per-request memory budget / backpressure; `gc` never tuned; library never frees mid-graph | both | both | Medium |

The single most important structural fact: **for the issue #300 pipeline, the
load_collection cube, the NDVI cube, and the aggregate cube are all alive
simultaneously at `save_result` time**, because each is pinned by `results_cache`
(library) *and* by the downstream stack that re-references its `ImageData`. Peak ≈ Σ
of all stages, not the working set.

---

## 1. Lifetime / reference map (one request: load → NDVI → aggregate_temporal → reduce → save_result)

The graph is evaluated by `OpenEOProcessGraph.to_callable(...)(named_parameters=...)`
([factory.py:1314-1319](../../titiler/openeo/factory.py#L1314-L1319)). The library
walks the DAG and, for **every** node, does:

```python
# .venv/.../openeo_pg_parser_networkx/graph.py
results_cache[node] = result          # L475  — primary retention, full eval lifetime
...
processed_result['info'] = result     # L464  — for non-xarray/dask results (our case)
results_cache_node._info = result     # L471
self.workflow.add_data(results_cache_node)  # L473 — SECOND reference, lives on self.workflow
```

So each node's return object is held **twice** for the whole evaluation, and nothing
is ever deleted from `results_cache` or `self.workflow` until the callable returns
and `parsed_graph` goes out of scope. There is no reference-counting / "last consumer"
eviction (the only special-case is *re-running* aggregate nodes, graph.py:410-414,
which makes things worse, not better).

### Stage-by-stage (issue #300: 3-yr extent → NDVI → aggregate_temporal(3 intervals) → reduce('t') → GTiff)

| Stage | Object produced | What holds it | When it *could* die | When it *actually* dies |
|-------|-----------------|---------------|---------------------|-------------------------|
| `load_collection` | `RasterStack` A (lazy; up to `MAX_ITEMS` date-groups) | `results_cache[load]`, `workflow` | after NDVI consumes it | end of request |
| (realize) | A`._data_cache[ts]` = source `ImageData` per slice | A, and B's `ImageData` (shared, see below) | after NDVI reads each slice | end of request |
| `ndvi` | `RasterStack` B = `from_images({ts: ndvi_img})` | `results_cache[ndvi]`, `workflow`, and **iterates `A.items()`** | after aggregate consumes it | end of request |
| `aggregate_temporal` | `RasterStack` C (3 slices) + transient `sub_stack` per interval | `results_cache[agg]`, `workflow` | after reduce consumes it | end of request |
| `reduce_dimension('t')` | `RasterStack`/`ImageData` D (1 slice) | `results_cache[reduce]`, `workflow` | after save consumes it | end of request |
| `save_result` | `SaveResultData` (bytes) | return value | after Response written | after Response |

Key reference-sharing facts (read from the code):

- **NDVI realizes the entire source stack.** `ndvi()` iterates `data.items()`
  ([indices.py:75](../../titiler/openeo/processes/implementations/indices.py#L75)).
  `RasterStack.items()` calls `_execute_selected_tasks(all_keys)`
  ([data_model.py:618-629](../../titiler/openeo/processes/implementations/data_model.py#L618-L629)),
  which populates `A._data_cache` with **every** source slice and leaves them there.
- **`from_images` pre-populates a new cache by reference.** B's cache is
  `dict(images)` ([data_model.py:787](../../titiler/openeo/processes/implementations/data_model.py#L787));
  the NDVI `ImageData` objects are new, but A's source `ImageData` remain cached in A.
  Both stacks are pinned by `results_cache`, so source + NDVI cubes coexist.
- **`filter_keys` carries over cached data** into the child stack
  ([data_model.py:725-730](../../titiler/openeo/processes/implementations/data_model.py#L725-L730))
  and the parent keeps its own copy — references multiply, never shrink.
- **`aggregate_temporal` builds a `sub_stack` per interval** from already-cached
  slices ([reduce.py:947-957](../../titiler/openeo/processes/implementations/reduce.py#L947-L957)).
  The sub-stacks share `ImageData` (no pixel copy) and are short-lived per iteration —
  this part is *not* a leak — but the prefetch at
  [reduce.py:931-933](../../titiler/openeo/processes/implementations/reduce.py#L931-L933)
  forces **all** in-interval slices of B resident at once before the loop starts.

**Peak number of full-resolution cubes alive at once:** for #300, effectively
**two full cubes** (source A + NDVI B, both `MAX_ITEMS` slices) plus the small
aggregate/reduce outputs — held simultaneously at the moment `aggregate_temporal`
runs and never released until the request ends.

---

## 2. Copy / amplification inventory

| Location | Operation | Extra peak bytes (per call) |
|----------|-----------|------------------------------|
| [math.py:314](../../titiler/openeo/processes/implementations/math.py#L314) `normalized_difference` `(x-y)/(x+y)` | uint16→**float64** result + 2 transient float64 temporaries | output = `H·W·8` per slice (4× a uint16 source) + ~2× transient during the expression |
| [reduce.py:498-503](../../titiler/openeo/processes/implementations/reduce.py#L498-L503) `_reduce_spectral_dimension_stack` | `numpy.stack(images)` then `moveaxis` | `stack` materializes a contiguous copy of **all slices** = `T·bands·H·W·dtype`; `moveaxis` is a view (cheap) but the reducer may copy again |
| [io.py:317](../../titiler/openeo/processes/implementations/io.py#L317) `_handle_raster_geotiff` | `numpy.ma.concatenate` of every slice | full copy of the multi-band result `T·H·W·dtype` (+ mask) |
| [io.py:124-130](../../titiler/openeo/processes/implementations/io.py#L124-L130) GTiff nodata bake | `arr.filled(nodata)` builds a new full array, wrapped in a fresh masked array | `+1×` the result array |
| [io.py:202-208](../../titiler/openeo/processes/implementations/io.py#L202-L208) PNG/JPEG | `.data.astype("uint8")` + new masked array | `+1×` (down-cast, transient) |
| [reduce.py:197-202](../../titiler/openeo/processes/implementations/reduce.py#L197-L202) `_feed_image_to_pixsel` resize | `resize_array(data)` + `resize_array(mask*1)` | `+2×` the slice, only when sizes mismatch |
| [reduce.py:121-126](../../titiler/openeo/processes/implementations/reduce.py#L121-L126) cutline aggregation | `valid_masks[0].copy()` then `minimum` per mask | `+1×` an `H·W` bool mask |
| [arrays.py:314](../../titiler/openeo/processes/implementations/arrays.py#L314), [math.py:366,381](../../titiler/openeo/processes/implementations/math.py#L366) | `numpy.ma.concatenate` / `numpy.stack` in `merge_cubes`/min/max | full copy of the operand set |

Masked arrays carry **data + mask** (`mask` is 1 byte/element unless it collapses to
scalar `nomask`). Treat every resident masked slice as ≈ `dtype_bytes + 1` per pixel
per band.

**The dominant amplifier is #4 (NDVI float64).** A 2-band uint16 source slice
(1024×1024) is `2·1M·(2+1) ≈ 6.3 MB`; its NDVI output is 1-band float64 =
`1M·(8+1) ≈ 9 MB` — *larger than the 2-band source it came from*, and it is retained
for the rest of the graph alongside the source.

---

## 3. Eviction gaps

- **`RasterStack._data_cache` has no eviction at all.** It is written in
  `__getitem__`/`_execute_selected_tasks`/`_execute_all_tasks`/`values`/`items`
  ([data_model.py:585-586, 516-517, 535-536, 607-629](../../titiler/openeo/processes/implementations/data_model.py#L585-L629))
  and read everywhere; there is **no `del`, no `pop`, no `clear`, no `__delitem__`**
  in the class. Once realized, a slice lives until the whole `RasterStack` is GC'd.
- **`ImageRef._image` is write-once, never cleared** ([data_model.py:221](../../titiler/openeo/processes/implementations/data_model.py#L221));
  a realized ImageRef pins its `ImageData` for the ref's life.
- **`filter_keys`/`from_images` populate the child cache but never drain the parent**
  ([data_model.py:725-730, 787](../../titiler/openeo/processes/implementations/data_model.py#L725-L730)).
  Parent + child both retain.
- **Graph `results_cache` + `workflow` never evict** (§1) — the system-level eviction
  gap: even if a `RasterStack` *could* be dropped after its consumer runs, the library
  holds two references to it until the request ends.
- `grep` for `del`, `gc.`, `.clear()`, `weakref` across
  `titiler/openeo/processes/implementations/` returns **nothing** relevant — no manual
  memory management anywhere in the pipeline.

Net: within a single request, retained memory is **monotonically non-decreasing**
until the response is produced.

---

## 4. Native vs Python heap

Native (off-heap, invisible to Python `gc`) caches, from
[deployment/k8s/charts/values.yaml:90-99](../../deployment/k8s/charts/values.yaml#L90-L99)
(the production target):

| Var | k8s value | Meaning | Worst-case footprint |
|-----|-----------|---------|----------------------|
| `GDAL_CACHEMAX` | `200` (MB) | global block cache | ~200 MB, process-wide |
| `VSI_CACHE_SIZE` | `5000000` (5 MB) | **per file-handle** chunk cache | 5 MB × (concurrent open handles) |
| `VSI_CACHE` | TRUE | enables the above | — |
| `CPL_VSIL_CURL_CACHE_SIZE` | *(unset in k8s)* | global curl LRU | GDAL default 16 MB |

The `.env.*` / launch.json dev profiles set these **much** higher
(`VSI_CACHE_SIZE=536870912` = 512 MB per-handle, `CPL_VSIL_CURL_CACHE_SIZE=200000000`
= 200 MB) — if any of those leak into a deployment they alone approach the container
limit ([.env.cdse:10-16](../../.env.cdse#L10-L16)).

**`VSI_CACHE_SIZE` is the native trap:** it is per-file-handle, so with `MAX_ITEMS`
date-groups × bands per group × `RIO_TILER_MAX_THREADS` concurrent reads (the
`ThreadPoolExecutor` in `_execute_selected_tasks`,
[data_model.py:505](../../titiler/openeo/processes/implementations/data_model.py#L505),
and `mosaic_reader(threads=...)` per task), the number of simultaneously-open VSI
handles can be large. At the k8s 5 MB it is modest (~hundreds of MB at most); at the
dev 512 MB it is catastrophic.

**Verdict:** for the #300 report the OOM is **primarily Python heap** (the retained
cubes, §1+§4-of-heap), with native caches as a secondary, mostly-bounded contributor
*on the k8s config* — but a primary contributor if a dev `VSI_CACHE_SIZE` is shipped.
The two need different fixes and look identical to the OOM killer, so the measurement
plan (§7) separates them via RSS-vs-tracemalloc.

---

## 5. gc reality check

- **No reference cycles are required to explain the retention** — the cubes are held by
  *live, reachable* references (`results_cache`, `workflow`, child stacks). `gc` running
  or not is irrelevant; this is not a cycle/finalizer problem, it's a "still reachable"
  problem. Tuning `gc` will **not** help.
- Potential cycles do exist but are minor: the closures in
  `make_mosaic_task`/`make_task_executor` capture `date_items`/`task_fn`
  ([stacapi.py:810-845](../../titiler/openeo/stacapi.py#L810-L845),
  [data_model.py:433-440](../../titiler/openeo/processes/implementations/data_model.py#L433-L440))
  and `_cached_image_task` captures the realized `ImageData`
  ([data_model.py:42-53](../../titiler/openeo/processes/implementations/data_model.py#L42-L53)).
  These keep STAC `Item` objects and pixel arrays alive for the stack's life but are
  reachable (held by `_tasks`/`_image_refs`), not garbage cycles.
- `gc` is **never** imported, tuned, or disabled in `titiler/openeo` (grep confirms).
  Relying on `gc` to reclaim the large objects is moot because they're reachable.

---

## 6. Concurrency × size budget — worst-case model

Per-slice resident bytes (masked array, data+mask):

```
slice_bytes(stage) = H · W · bands(stage) · (dtype_bytes(stage) + mask_byte)
  source   : bands=B_src, dtype=uint16 (2)  -> H·W·B_src·3
  ndvi     : bands=1,     dtype=float64 (8)  -> H·W·9
```

Per-request **sustained heap** (the cubes that coexist at peak), for #300:

```
peak_heap ≈ results_cache pins {
              A: N · H·W·B_src·3          # source cube, all N=#date-groups slices
            + B: N · H·W·9               # NDVI float64 cube, all N slices
            + C: I · H·W·9               # aggregate, I = #intervals (3)
            } × 2                        # ×2: results_cache AND workflow each hold a ref*
          + transient copies (§2)        # numpy.stack/concatenate spikes
          + python object overhead
```

\* the ×2 is references to the *same* objects, so it does not double pixel RAM, but it
**defeats any attempt to drop a cube early** unless *both* holders release it.

Plugging the production limits — `MAX_PIXELS = 1.5e9`
([launch.json](../../.vscode/launch.json), CDSE), `MAX_ITEMS = 100`,
default 1024×1024:

- `load_collection` admits a request iff `W·H·N ≤ MAX_PIXELS`
  ([stacapi.py:763-773](../../titiler/openeo/stacapi.py#L763-L773)). With
  `MAX_PIXELS=1.5e9` that permits `N ≤ 1.5e9/(1024·1024) ≈ 1430` slices — but `N` is
  separately capped at `MAX_ITEMS=100` date-groups.
- At `N=100`, `H=W=1024`, `B_src=2`:
  - source cube A ≈ `100 · 1M · 2 · 3` = **629 MB**
  - NDVI cube B ≈ `100 · 1M · 9` = **900 MB**
  - aggregate C ≈ negligible (~27 MB)
  - **sustained heap ≈ 1.56 GB for ONE request**, before transient `numpy.stack`
    spikes and native caches.
- Container limit = **2 Gi**, requests = limits
  ([values.yaml:126-132](../../deployment/k8s/charts/values.yaml#L126-L132)).
  1.56 GB heap + ~200–400 MB native + Python/runtime baseline (~200–300 MB) → **at or
  over 2 Gi for a single in-flight #300 request.**

**Concurrency multiplier.** The helm args do **not** pass `--workers` and
`values.yaml` does **not** set `WEB_CONCURRENCY`
([values.yaml:90-99](../../deployment/k8s/charts/values.yaml#L90-L99)), so the
container runs a single uvicorn worker. But the graph callable is synchronous and
several requests can be in flight concurrently (event-loop threadpool); each carries
its own `parsed_graph`/`results_cache`, so:

```
RSS ≈ workers · in_flight_per_worker · peak_heap_per_request
    + GDAL_CACHEMAX (process-global, once)
    + VSI_CACHE_SIZE · open_handles
```

With a single worker, **two** concurrent #300 requests (≈3.1 GB) already exceed 2 Gi.
The dev profiles with `WEB_CONCURRENCY=32` ([launch.json](../../.vscode/launch.json))
would be hopeless at this per-request cost — but those are dev, not k8s.

**The load that triggers OOM:** a single wide-extent request that resolves to many
date-groups (large `N`) with an NDVI/index step, *or* a small number of concurrent
such requests. `MAX_PIXELS=1.5e9` is the policy bug: it is ~1000× larger than the heap
the 2 Gi container can hold once you account for #cubes × dtype × mask.

---

## 7. Prioritized recommendations

Ranked; each tagged **[heap|native]** and **[peak|sustained]**.

### P0 — stop retaining whole intermediate cubes  [heap][sustained]

The root cause. Two complementary levers:

1. **Down-convert NDVI/indices to float32** (and avoid the uint16-wrap risk) — halves
   the largest cube and fixes a latent correctness bug.
   *Illustrative — not applied:*

   ```python
   # math.py normalized_difference
   def normalized_difference(x, y):
       x = x.astype("float32"); y = y.astype("float32")   # promote BEFORE subtract/add
       return (x - y) / (x + y)
   ```

   Cuts cube B from 900 MB → ~500 MB and removes uint16 overflow in `x+y` / underflow
   in `x-y`.

2. **Evict realized slices once consumed.** Give `RasterStack` an explicit
   `release(key)` / `clear()` and call it when a node's output is the sole consumer.
   The blocker is the library: `results_cache[node]`/`workflow` keep the parent stack
   reachable (§1), so `del` inside our process functions is insufficient. Options, in
   order of effort:
   - **Cheapest:** after building the downstream stack, proactively clear the *parent*
     stack's `_data_cache`/`_image_refs` from within the consuming process (e.g. at the
     end of `ndvi()`/`aggregate_temporal()` clear the input's cache). The library still
     holds the now-empty `RasterStack` shell, but the pixel arrays are freed.
   - **Proper:** wrap evaluation so `results_cache` is pruned by out-degree (drop a
     node's entry once all its graph successors have run). This likely needs an upstream
     change or a custom walk; verify `OpenEOProcessGraph` exposes successor counts
     before committing.
   - Confirm whether `self.workflow` retention (graph.py:462-473) can be disabled
     (profiling is opt-in in some versions) — it is pure overhead for our object types.

### P1 — fix the pixel budget so it reflects real RAM  [policy → heap]

`MAX_PIXELS` currently bounds only `W·H·N` ([stacapi.py:766](../../titiler/openeo/stacapi.py#L766)).
Make the admission test a **byte** budget that accounts for #intermediate cubes, dtype
width and masks, tied to the container limit:

```
est_bytes ≈ N · H · W · (B_src·3 + 9 /*ndvi float64*/) · safety_factor
reject if est_bytes > MEM_BUDGET   # e.g. 0.6 · container_limit / max_in_flight
```

At minimum, **lower `MAX_PIXELS`** in the production config to something the 2 Gi
limit can survive (≈1.5e8, not 1.5e9) and document the per-cube multiplier. This is the
fastest single mitigation. **[peak]+[sustained]**

### P2 — stream the spectral reducer / GTiff assembly  [heap][peak]

`apply_pixel_selection` already streams via `PixelSelectionMethod`
([reduce.py:267-281](../../titiler/openeo/processes/implementations/reduce.py#L267-L281)) —
good. But:

- `_reduce_spectral_dimension_stack` materializes `numpy.stack(images)` of **all**
  slices ([reduce.py:498](../../titiler/openeo/processes/implementations/reduce.py#L498)).
  For large stacks, feed the reducer incrementally where the reducer permits (respecting
  the "call once" contract documented at the top of reduce.py).
- `_handle_raster_geotiff` `numpy.ma.concatenate`s every slice
  ([io.py:317](../../titiler/openeo/processes/implementations/io.py#L317)); for many
  bands consider writing band-by-band to the GDAL dataset instead of building one giant
  array.

### P3 — bound native caches to the container  [native][peak]

- Keep `GDAL_CACHEMAX` modest (200 MB k8s is fine) and **document that
  `VSI_CACHE_SIZE` is per-handle** — the 512 MB dev value
  ([.env.cdse:15](../../.env.cdse#L15)) must never reach prod. Prefer the 5 MB k8s value
  and cap concurrent handles (`RIO_TILER_MAX_THREADS`).
- Pin `CPL_VSIL_CURL_CACHE_SIZE` explicitly in k8s (it's unset) so it doesn't inherit a
  surprising default.

### P4 — per-request backpressure (reject, don't OOM)  [both][peak]

Add a lightweight in-process semaphore / memory-aware admission so concurrent heavy
graphs are queued/rejected with `413/429` rather than racing into the OOM killer. Tie
the limit to `MEM_BUDGET / peak_heap_per_request` from §6. Currently nothing bounds
in-flight heavy requests.

### P5 — measurement plan (do this first to confirm the split)  [both]

1. **tracemalloc snapshots** around each node: patch the process registry / wrap
   `prebaked_process_impl` to `tracemalloc.take_snapshot()` before/after each node;
   diff to attribute heap growth to `load_collection` vs `ndvi` vs `aggregate_temporal`.
2. **memray** on a real #300 graph (`memray run -m uvicorn ...` or around a direct
   `pg_callable` call) for a flamegraph of allocation sites and high-water mark.
3. **RSS vs tracemalloc**: log `resource.getrusage().ru_maxrss` (RSS, includes native)
   alongside `tracemalloc.get_traced_memory()` (heap only). `RSS − tracemalloc` ≈ native
   (GDAL/VSI) — this is the definitive heap-vs-native split for OOM triage.
4. **Retention proof**: `len(results_cache)` and `sum(a.nbytes for a in arrays)` at
   `save_result` time; `objgraph.show_backrefs` on a sample source `ImageData` to show
   it's pinned by both `results_cache` and the child stack.
5. **GDAL reporting**: `gdal.GetCacheUsed()` / `gdal.GetCacheMax()` sampled during a
   request to quantify the block-cache contribution.

---

## 8. Ranked TODO (issue/PR candidates)

1. **[heap][sustained] P0** — NDVI/indices → float32 + promote-before-arithmetic
   ([math.py:314](../../titiler/openeo/processes/implementations/math.py#L314)).
   Smallest diff, ~400 MB saved on #300, also fixes uint16 wrap correctness bug. *PR-ready.*
2. **[policy][peak+sustained] P1** — replace/augment `MAX_PIXELS` with a byte budget
   accounting for #cubes·dtype·mask; lower the prod value meanwhile
   ([stacapi.py:763-773](../../titiler/openeo/stacapi.py#L763-L773)). *PR-ready (config) + design (budget).*
3. **[heap][sustained] P0** — `RasterStack.release()/clear()` + proactively drop the
   parent stack's `_data_cache`/`_image_refs` in `ndvi`/`aggregate_temporal` after the
   child stack is built ([data_model.py](../../titiler/openeo/processes/implementations/data_model.py),
   [indices.py:73-77](../../titiler/openeo/processes/implementations/indices.py#L73-L77)).
   *Design — verify it doesn't break shared-ImageData consumers.*
4. **[heap][sustained] P0** — investigate pruning `results_cache`/disabling `workflow`
   `_info` retention in the graph walk (graph.py:462-475); may need upstream change.
   *Investigation.*
5. **[native][peak] P3** — document `VSI_CACHE_SIZE` per-handle semantics; ensure prod
   never inherits the 512 MB dev value; pin `CPL_VSIL_CURL_CACHE_SIZE`
   ([values.yaml:90-99](../../deployment/k8s/charts/values.yaml#L90-L99),
   [.env.cdse](../../.env.cdse)). *PR-ready (config/docs).*
6. **[both][peak] P4** — in-process memory-aware admission/backpressure (reject heavy
   concurrent graphs). *Design.*
7. **[heap][peak] P2** — incremental spectral reduction / band-by-band GTiff write
   ([reduce.py:498](../../titiler/openeo/processes/implementations/reduce.py#L498),
   [io.py:317](../../titiler/openeo/processes/implementations/io.py#L317)). *Design.*
8. **[both] P5** — land the measurement harness (tracemalloc per-node + RSS/tracemalloc
   split + memray profile) to confirm the heap-vs-native split and guard regressions.
   *Tooling; do first.*

```
