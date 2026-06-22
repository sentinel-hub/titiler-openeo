# Memory management

When titiler-openeo evaluates a process graph it materializes raster cubes
(`ImageData` / numpy arrays) as intermediate results. This page explains how peak
memory is kept close to the *working set* rather than the sum of every
intermediate, and the knob that controls it.

This page is about the Python heap (the cubes). The off-heap GDAL/VSI caches are
a separate concern, tuned via deployment configuration.

## The problem

The openEO graph engine stores every node's return value in an internal
`results_cache` for the entire evaluation and never releases it. A linear
pipeline such as

```
load_collection → ndvi → aggregate_temporal → reduce_dimension → save_result
```

therefore keeps the source cube, the NDVI cube, and the aggregate cube all
resident at once, even though each is only needed by the next step. Peak memory
grows with the **sum of all intermediate cubes**, which is what drives
out-of-memory kills on wide spatial/temporal extents.

## Reference-counted eviction

titiler-openeo wraps the engine's `results_cache` with a reference-counted cache
that frees a node's data **as soon as the graph topology proves every consumer of
that node has run**. Because a result is only freed *after* its last consumer, no
node ever re-reads an evicted result — there are no re-fetches. Peak memory drops
to roughly the working set (the cubes actually in flight).

Freeing is done by emptying the `RasterStack` in place, which also releases the
second reference the engine keeps internally (so the cube is truly collectable,
not just dropped from one dict).

### Safety

The cache is conservative by construction:

- **Only frees fully-consumed results** — counted from `ResultReference` edges in
  the graph.
- **Never frees a still-reachable object** — e.g. when a process returns its
  input unchanged, or when a node returns the final result.
- **Disables itself for re-executing graphs** — graphs containing
  `aggregate_spatial`, which the engine re-executes and would re-read, fall back
  to the original keep-everything behavior.

## Configuration

Eviction is **on by default**. To restore the engine's original behavior (keep
every intermediate for the whole evaluation), set:

```bash
TITILER_OPENEO_PROCESSING_EVICT_INTERMEDIATE_RESULTS=false
```

## Related: float32 index math

Index/derived bands (`ndvi`, `divide`, `normalized_difference`, …) are computed
in **float32** rather than numpy's default float64 when their inputs are integer
rasters, halving those cubes while keeping source rasters at their compact
integer dtype. Existing floating-point inputs keep their precision.
