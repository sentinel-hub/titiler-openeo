# ADR 0015 — Distributed graph execution without a heavy runtime

- **Status:** Proposed
- **Date:** 2026-11-13
- **Deciders:** @emmanuelmathot, @vincentsarago
- **Related:** ADR 0011 (request cancellation), `docs/src/memory-management.md`,
  `docs/audits/async-concurrency-audit.md`

---

## 1. Context

### 1.1 The need

Some process graphs are too heavy for one pod:

- a large area of interest, or a long time series, in one synchronous
  `/result` request;
- a future batch job that covers a region;
- a burst of XYZ tile requests on one service.

The question of this ADR: can the backend spread one graph over several
pods, so that wall time and memory are divided, while the arrays stay numpy
arrays in memory and the architecture stays small? The answer must not be
Dask, Ray, Spark, Beam or Legate. These runtimes bring a scheduler, a
cluster state service, and a second array model. This ADR records a watch of
what exists in 2026, a decision, and the numbers of one spike.

### 1.2 What exists

- One process graph runs in one Python process. The graph walker
  (`openeo_pg_parser_networkx`, called from `titiler/openeo/factory.py`) is
  depth-first and sequential. Each node result is memoized in a shared
  `results_cache`.
- Data is a `RasterStack`: a dict of `datetime` to `ImageData`, where each
  slice is a lazy `ImageRef` task
  (`processes/implementations/data_model.py`). A read happens when a process
  needs the pixels. `load_collection` makes one task for each date, and each
  task is a `mosaic_reader` over the items of that date (`stacapi.py`).
- Parallelism is a `ThreadPoolExecutor(MAX_THREADS)` for each stack access.
  `MAX_THREADS` is the rio-tiler default: 5 threads for each CPU. There is
  no parallelism between nodes and no parallelism across processes or pods.
- Memory peaks when a process stacks the whole cube `(t, b, h, w)`:
  `apply` (`apply.py`), `max`, `min` and the other array reducers
  (`math.py`), and the spectral reduce (`reduce.py`). `mean`, `median`,
  `sd`, `count` and the pixel selections stream one slice at a time
  (`apply_pixel_selection`, `reduce.py`).
- Limits: `max_pixels = 1e8` and `max_items = 20` (`settings.py`).
- Deployment: N identical pods behind one Service, `replicaCount: 1`, HPA
  off by default, 1 CPU and 2 GiB each (`deployment/k8s/charts/values.yaml`).
  Pods share only Postgres. There is no queue, no `/jobs` tier, and no
  worker role.

### 1.3 Constraints

- No new stateful component in the default deployment. Postgres is the only
  shared service.
- numpy masked arrays stay the data model. No xarray, no Zarr-backed array
  type in the request path.
- A synchronous request must stay a request: no job that starts pods.
- Python 3.14, the version of the image.

## 2. Where parallelism lives

### 2.1 The unit of distribution

A process graph is a chain with few branches. One node for each pod gives
little: most nodes wait for the previous node. The gain is in the data: run
the **same sub-graph on several spatial windows** (or time slices) on
several pods, then put the results together. XYZ tile services already do
this for each tile. The difference is only who cuts the extent and who
gathers the parts.

### 2.2 Chunk-safe processes

A graph is "chunk-safe" up to the first node that needs pixels from another
window. The implemented processes fall in four classes:

| Class | Processes | Spatial windows | Time slices |
|---|---|---|---|
| Pixel-local | band math (`add`, `multiply`, `ndvi`, `normalized_difference`, `linear_scale_range`, `clip`, `if_`, `mask`, ...), `apply`, `apply_dimension`, `reduce_dimension(bands)`, `array_*`, `filter_bands`, `filter_temporal`, `rename_labels`, `add_dimension`, `merge_cubes` (same grid), `colormap`, `color_formula`, `mask_polygon`, `filter_bbox` | yes | yes |
| Time-local for each pixel | `reduce_dimension(t)` with any reducer, `aggregate_temporal`, `aggregate_temporal_period`, `array_interpolate_linear` over `t`, the pixel selections (`first`, `last`, `highestpixel`, ...) | yes | only for associative reducers (`max`, `min`, `sum`, `count`, `mean` as sum and count); not `median`, `sd`, `quantiles` |
| Neighbourhood | `apply_kernel`, `slope`, `aspect`, `hillshade`, `resample_spatial`, `resample_cube_spatial` | yes with a halo of the kernel radius; the resamples need a halo of the interpolation support | yes |
| Global | `aggregate_spatial` (zonal statistics), `save_result`, `load_collection`, `load_url`, `tile_assignment` | `aggregate_spatial`: partial sums merge for `sum`, `count`, `mean`, `min`, `max`; not for `median`. `save_result` is the gather point. The loaders read one window naturally | — |

`sar_backscatter` works on each pixel in the present implementation. A
terrain correction would make it a neighbourhood process.

Most user graphs are a loader, some pixel-local nodes, one time reducer and
`save_result`. They are chunk-safe end to end.

### 2.3 The cost of moving data

Each window result must come back. The result of a time reducer is small:
one image. The result of `apply` on a cube is the whole cube, and moving it
is slower than computing it again. So the rule is **scatter the graph,
gather the result**: a worker runs the chunk-safe prefix up to the gather
point and returns one image. There are no shuffles between workers.

For one float32 window of 1024 x 1024 x 4 bands (16 MiB) plus mask, pickle
protocol 5 with out-of-band buffers costs about 145 ms on 1 Gbit/s and
about 20 ms on 10 Gbit/s, transfer included (see §3.2). The split pays only
when the work of one window is well above 100 ms, or when the output is a
reduce.

### 2.4 Memory arithmetic

Today one pod holds `t x b x h x w x itemsize` when a reducer stacks the
cube, and more for the copies of the stack. With K windows each pod holds
`1/K` of the cube plus a fixed cost (interpreter, GDAL cache). §5 measures
this. The coordinator holds only the gathered output.

Distribution does not replace streaming reducers. A streaming `max` over
`t` divides memory by `t` on one pod, with no network. Both are useful.

## 3. Technology watch (2026-11-13)

Desk research. Versions and dates were checked on PyPI, GitHub release pages
and official documents on the date above. Heavy runtimes were excluded by
decision (§1.1).

### 3.1 Who runs a chunk

| Candidate | New infrastructure | numpy transport | Memory bound | Python 3.14 | Verdict |
|---|---|---|---|---|---|
| Self-as-worker HTTP fan-out (own Service; KEDA HTTP add-on v0.16.0 optional) | none | bytes over HTTP, one copy each side | each worker holds 1/K; coordinator gathers | n/a | **Adopt now**, with a separate chunk pool and a fan-out depth of 1 |
| pgqueuer 2.0.0 (2026-11-09, MIT, async, asyncpg or psycopg3) | none, uses Postgres | bytes payload or blob reference | no | 3.11–3.15 | **Spike later** for batch jobs |
| Chancy 0.26.0 (2026-10-14, MIT) | none | Postgres rows | per-job memory limits | 3.11–3.14 | Spike later; one maintainer |
| Procrastinate 3.11.0 (MIT) | none | no result channel | no | yes | Reject |
| PGMQ 1.13.1 + Python client 0.10.0 (2025-03) | SQL schema | jsonb | no | client stops at 3.12 | Reject |
| Celery 5.7, Dramatiq 2.2, RQ 3.0, Huey 3.5, arq 0.28, TaskIQ 0.13, NATS JetStream (nats-py 2.16) | Redis, RabbitMQ or NATS, plus a result backend | pickle | no | most yes; nats-py stops at 3.13 | Reject: a new stateful service only to move bytes. If a broker comes later: TaskIQ + NATS Object Store |
| Lithops 3.9.0 (2026-10-30, Apache-2.0), Kubernetes backend | object store, runtime image, RBAC to create Jobs | cloudpickle through the store | each pod | 3.10–3.14 | Spike later; cold Jobs do not fit a synchronous request |
| Cubed 0.28.0 (2026-05-29, Apache-2.0); executors without heavy runtime: `threads`, `processes`, `lithops`, `modal`, `coiled`, `globus-compute` | shared Zarr store for every intermediate | Zarr chunks | yes, checked at plan time | >= 3.12 | Spike later; the only candidate that bounds memory, but it replaces the numpy pipeline |
| Modal, Coiled Functions, Globus Compute | SaaS control plane | pickle | partial | yes | Reject |
| Parsl 2026.11 | interchange process, worker pods | pickle, dill | no | 3.10+ | Reject |
| mpi4py 4.1.2 + Kubeflow MPI Operator or Volcano | operator, SSH, gang scheduling | true zero-copy | no | yes | Reject for request/response; the reference for a batch tier |

Known pitfalls of the HTTP fan-out, and the guards:

- **Self-induced deadlock.** When every pod is coordinator and worker, a
  parent request holds a slot while it waits for children that need a slot
  from the same pool. Guard: the chunk endpoint has its own semaphore, or a
  separate worker Deployment of the same image; a chunk never fans out
  again (depth 1); hard connect and read timeouts.
- **No back-pressure.** The coordinator bounds the chunks in flight. HPA
  and KEDA react after 30 to 60 s, so a burst lands on the pods that exist.
- **Authentication.** The coordinator does not replay the user token. It
  signs a short-lived internal token for the chunk request.
- **Gather memory.** The coordinator still holds the full output. For a
  large output, write the windows into the output file as they arrive.

Sources: [KEDA HTTP add-on](https://keda.sh/http-add-on/latest/),
[pgqueuer](https://github.com/janbjorge/pgqueuer),
[Chancy](https://github.com/TkTech/chancy),
[Procrastinate](https://github.com/procrastinate-org/procrastinate),
[PGMQ](https://github.com/pgmq/pgmq),
[Lithops design](https://lithops-cloud.github.io/docs/source/design.html),
[Cubed executors](https://cubed-dev.github.io/cubed/user-guide/executors.html),
[Parsl](https://github.com/Parsl/parsl),
[Kubeflow MPI Operator](https://github.com/kubeflow/mpi-operator),
[self-induced deadlocks](https://dzone.com/articles/thread-pool-self-induced-deadlocks).

### 3.2 How arrays move

| Option | Version | License | Zero-copy on receive | `numpy.ma` mask | Verdict |
|---|---|---|---|---|---|
| pickle protocol 5, out-of-band buffers (stdlib; default protocol in 3.14) | stdlib | PSF | yes, numpy wraps the received memoryview | native: data, mask and fill value travel together | **Adopt** between pods. Unsafe on untrusted input, so internal network and a signed request only |
| raw `.npy` bytes (`numpy.lib.format`) | numpy | BSD | yes, `frombuffer` | no, the mask is a second array | **Adopt** as the debug and interoperability format |
| blosc2 4.15.0 (2026-11-11) | 4.15.0 | BSD-3 | decompress into a preallocated array | codec only | **Adopt** as an optional codec (LZ4 + shuffle). Break-even on 10 Gbit/s |
| zstandard 0.25.0; `compression.zstd` in the 3.14 stdlib | 0.25.0 | BSD-3, PSF | codec | codec only | Adopt for cold intermediates on object storage |
| Apache Arrow IPC, FixedShapeTensor, Flight (pyarrow 26.0.1, 2026-11-03) | 26.0.1 | Apache-2.0 | yes for IPC buffers | validity bitmap is for each tensor, not for each pixel | Spike only if Flight is wanted. A 40 MB wheel for little gain |
| gRPC bytes fields | current | Apache-2.0 | no, protobuf copies; 4 MiB default limit | no | Reject |
| msgpack-numpy 0.4.8 (2022) | 0.4.8 | BSD | partial | no | Reject, not maintained |
| Zarr v3 (zarr-python 3.4.2, 2026-10-27) on object storage | 3.4.2 | MIT | no | `fill_value` only; a sibling array for the mask | Spike for batch jobs whose gather exceeds one pod |
| Icechunk 2.3.1, tensorstore 0.1.86, safetensors 0.8.0, xpublish 0.5.2 | — | — | no | no | Reject for this use |
| COG on object storage (rasterio) | present | — | no | nodata or internal mask band, native | Adopt for final results |
| `multiprocessing.shared_memory` (stdlib, `track=` since 3.13) | stdlib | PSF | yes | two blocks | **Adopt** first, inside one pod, for `WEB_CONCURRENCY > 1` |
| SharedArray 3.2.4 (GPLv2), Arrow Plasma (removed in Arrow 12) | — | — | — | — | Reject |

No transport has a native pixel mask. The envelope is: a small header
(dtype, shape, CRS, bounds, nodata, mask encoding), the data buffer, and an
optional mask buffer (bit-packed, 0.5 MiB for 1024 x 1024 x 4). pickle 5
gives this for free with `numpy.ma`.

Cost model for one 16 MiB window plus mask (memcpy 5 GB/s, blosc2 LZ4 at
2 GB/s compress and 5 GB/s decompress, ratio 2x not verified on raster
data):

| Option | Serialize | Wire 1 Gbit/s | Wire 10 Gbit/s | Deserialize | Total 1 G / 10 G |
|---|---|---|---|---|---|
| pickle 5, no compression (16.5 MiB) | ~3 ms | ~139 ms | ~14 ms | ~0–3 ms | ~145 ms / ~20 ms |
| pickle 5 + blosc2 LZ4 (~8.3 MiB) | ~8 ms | ~70 ms | ~7 ms | ~4 ms | ~85 ms / ~20 ms |

Object storage as the gather medium wins when the fan-in exceeds the RAM of
one pod, when results must survive a pod restart, or when workers finish at
very different times. HTTP wins for a synchronous request: an S3 round trip
adds two request latencies, tens to hundreds of milliseconds.

Sources: [PEP 574](https://peps.python.org/pep-0574/),
[pickle, Python 3.14](https://docs.python.org/3.14/library/pickle.html),
[pyarrow](https://pypi.org/project/pyarrow/),
[FixedShapeTensorArray](https://arrow.apache.org/docs/python/generated/pyarrow.FixedShapeTensorArray.html),
[blosc2](https://pypi.org/project/blosc2/),
[blosc2 benchmarks](https://blosc.org/python-blosc2/guides/benchmarks.html),
[zarr](https://pypi.org/project/zarr/),
[Arrow 12 release notes](https://arrow.apache.org/release/12.0.0.html),
[gRPC performance](https://grpc.io/docs/guides/performance/),
[shared_memory](https://docs.python.org/3/library/multiprocessing.shared_memory.html),
[S3 pricing](https://aws.amazon.com/s3/pricing/).

### 3.3 GPU offload

| Item | Version | License | Python | `numpy.ma` | Verdict |
|---|---|---|---|---|---|
| CuPy 14.2.0 (2026-08, CUDA 12 and 13 wheels, ~135 MB; ROCm experimental) | 14.2.0 | MIT | 3.10–3.14 | **no** masked arrays; the issue was closed in 2026-03 with a pointer to `marray` | Spike |
| array-api-compat 1.16.0 (2026-10-25) | 1.16.0 | MIT | >= 3.10 | no | **Adopt** as the dispatch layer: `xp = array_namespace(a)` on the native numpy or cupy arrays |
| array-api-extra 0.11.5; array-api-strict 2.6.1 | — | BSD | — | NaN-aware reducers; no CuPy backend in strict | Adopt with compat; strict for tests only |
| marray 0.0.12 (2025-09) | 0.0.12 | MIT | — | yes, over any Array API library | Spike; reductions about 11x slower than NaN reducers; one maintainer |
| numba-cuda 0.30.4 (2026-07) | 0.30.4 | BSD-2 | 3.10–3.14 | no | Reject for now: maintenance mode, successor `numba-cuda-mlir` has no release |
| cuCIM 26.10 | 26.10 | Apache-2.0 | 3.11–3.14 | no | Reject: the operations are in CuPy already |
| cuSignal | merged into `cupyx.scipy.signal` | — | — | — | use CuPy |
| cupy-xarray | 0.1.x, "looking for maintainers" | Apache-2.0 | — | no | Reject, no xarray here |

Facts that shape the design:

- CuPy has no `numpy.ma`. The pattern is data plus a boolean mask, or NaN
  on the float path with `nanmax`, `nanmean`, and the mask put back at the
  output. This is also the pattern of the rasterio + CuPy guides: CPU read,
  GPU compute, CPU write.
- The Array API standard has no masked array, no convolution and no
  quantile. Convolution needs a second dispatch (`scipy.ndimage` or
  `cupyx.scipy.ndimage`).
- Transfer: about 20 to 25 GB/s on PCIe Gen4. A 1024 x 1024 x 20 float32
  tile (84 MB for each band) takes about 5 ms each way. One pass of band
  math over it takes tens of milliseconds on one CPU core. Band math and
  simple reducers gain less than 2x net. Convolution gains 10 to 60x
  (Carpentries, T4, 2048 x 2048, transfers included). Per-pixel least
  squares (`fit_curve`) and model inference are the strong cases.
- Amdahl: a request that spends 80 % in COG reads gains at most 1.23x from
  a 20x compute speedup. With the reads in a cache the picture changes.
- Kubernetes: the NVIDIA GPU Operator installs the driver, the device
  plugin and GPU Feature Discovery. A pod requests `nvidia.com/gpu: 1`.
  Time-slicing shares one T4 or L4 between small pods without memory
  isolation; MIG gives isolation on data-center parts. A second Deployment
  of the same image with a `nodeSelector` on `nvidia.com/gpu.present=true`
  and its own Service is how a coordinator finds a GPU worker. A GPU node
  costs 3 to 5x a 4 vCPU node (AWS g5.xlarge about $1.0/h, GCP
  g2-standard-4 about $0.8/h, Azure NC4as_T4_v3 about $0.5/h, 2026).
- No titiler or rio-tiler GPU work was found.

Sources: [CuPy install](https://docs.cupy.dev/en/stable/install.html),
[CuPy issue 2225](https://github.com/cupy/cupy/issues/2225),
[CuPy differences](https://docs.cupy.dev/en/stable/user_guide/difference.html),
[marray](https://pypi.org/project/marray/),
[array-api-compat](https://data-apis.org/array-api-compat/supported-array-libraries.html),
[NumPy Array API](https://numpy.org/doc/2.3/reference/array_api.html),
[numba-cuda](https://pypi.org/project/numba-cuda/),
[Carpentries CuPy lesson](https://carpentries-incubator.github.io/lesson-gpu-programming/cupy.html),
[GPU sharing](https://docs.nvidia.com/datacenter/cloud-native/gpu-operator/latest/gpu-sharing.html),
[GPU Feature Discovery](https://pkg.go.dev/github.com/NVIDIA/gpu-feature-discovery).

### 3.4 Result of the watch

No framework gives "scatter a sub-graph across sibling pods and gather
numpy" without new infrastructure or a change of array model. Cubed is the
only candidate that bounds memory, and it does so by putting every
intermediate in Zarr. The lightest path that respects §1.3 is the service
as its own worker, over HTTP, with explicit guards.

## 4. Decision

### 4.1 Scatter the graph, gather the result, on the pods we have

1. The coordinator is the pod that receives `/result`. It computes the
   chunk-safe prefix of the graph (§2.2). If the prefix is the whole graph
   up to `save_result`, and the extent is above a threshold (in pixels, in
   bytes of the stacked cube, or in dates), it splits the extent into K
   windows.
2. It sends the same graph with each window as `spatial_extent` to an
   internal chunk endpoint on its own Service. Workers run the graph as
   today, in process, with numpy. Each returns one `ImageData`.
3. The coordinator places the windows into the output grid and encodes the
   result. There is no mosaic and no resampling: the windows are cut on the
   output grid.
4. Below the threshold, or when the prefix is shorter than the graph, the
   graph runs in process as today. Nothing changes for small requests.

No new component. The Helm chart gains a second Deployment of the same
image, the worker pool, with its own Service and HPA. A deployment can also
point the coordinator at its own Service and accept the deadlock guards.

### 4.2 The seam in the code

Two small interfaces, so that the first PR changes no behavior:

```python
class GraphPartitioner(Protocol):
    def chunk_safe_prefix(self, graph: ProcessGraph) -> Optional[ProcessGraph]: ...
    def split(self, extent: SpatialExtent, k: int) -> list[Window]: ...

class ChunkExecutor(Protocol):
    def run(self, graph: ProcessGraph, windows: list[Window]) -> list[ImageData]: ...
```

Implementations, in order: `local` (threads in the same process, the
default, equal to today), `http` (the worker Service, §4.1), and later
`queue` (pgqueuer, for batch jobs). The partitioner uses the process
classes of §2.2, kept as a table next to the process registry.

### 4.3 Transport

The chunk endpoint returns the `ImageData` as pickle protocol 5 with
out-of-band buffers, optional blosc2 LZ4 codec, over HTTP/1.1 keep-alive
(httpx). The request carries a short-lived token signed by the coordinator.
The endpoint accepts only requests from the cluster network. A `.npy`
variant exists for debugging.

Inside one pod with `WEB_CONCURRENCY > 1`, `multiprocessing.shared_memory`
is the first step and needs no network.

### 4.4 Guards

- The chunk endpoint has its own concurrency limit, separate from
  `/result`. A chunk request never splits again.
- The coordinator bounds the chunks in flight to the size of the worker
  pool, and sets connect and read timeouts.
- Cancellation of the parent request (ADR 0011) cancels the chunk
  requests.
- `max_pixels` and `max_items` apply to each chunk; a new setting bounds
  the total of a split request.

### 4.5 GPU as a capability of a worker pool

A GPU is not a separate system. It is a worker pool with
`nvidia.com/gpu` in its resources and a capability label. The coordinator
sends a chunk there only when the graph contains a process of the strong
classes: `apply_kernel`, `fit_curve`, a UDF or model inference. In the
process code, `xp = array_api_compat.array_namespace(data)` selects numpy
or CuPy; masks travel as a boolean array next to the data, and `numpy.ma`
is rebuilt at the output. Band math and simple reducers stay on the CPU.
This part waits for a graph that needs it.

### 4.6 Batch jobs

When batch jobs arrive, the `queue` executor uses pgqueuer on the existing
Postgres, with the window results written as COGs to object storage and
gathered by the job finisher. Cubed stays the candidate to revisit if a
job must bound memory below one window.

### 4.7 Alternatives rejected

- **Dask, Ray, Spark, Beam, Legate**: excluded by decision (§1.1).
- **A broker queue for synchronous requests**: a new stateful service for
  the same bytes HTTP moves.
- **Cubed now**: it replaces the numpy pipeline and needs a shared store.
- **MPI**: batch only; a lost rank loses the job.
- **Node-level distribution**: the graph is a chain; no gain.

## 5. Spike (2026-11-13)

`scripts/spike_distributed/fanout_spike.py` runs the real process registry
and the real `node_callable`, as `scripts/debug_graph.py` does, on local
synthetic COGs. The graph is `reduce_dimension(max)` over `t`, the process
that stacks the whole cube. The data is 12 dates, 2 bands, 6144 x 6144
pixels, float32: a 3.6 GB cube plus a 0.9 GB mask. Each date has a
different nodata stripe, so the mask matters. Three transports run the same
work: `inproc` (one process, the baseline), `process` (K spawned
processes, pickle 5 out-of-band) and `http` (K uvicorn workers on
localhost, httpx fan-out). Every run compares the output to the baseline
with `numpy.array_equal` on data and mask.

Host: 22 CPUs, 62 GiB, local NVMe disk, Python 3.14.7, numpy 2.4.4,
rasterio 1.5.0, rio-tiler 9.0.4. Numbers are the best of two runs. All runs
were equal to the baseline, pixel for pixel.

### 5.1 All cores available to every process

| Transport | K | Wall (s) | Slowest worker (s) | Peak RSS of a worker (MiB) | Peak RSS of the coordinator (MiB) | Bytes gathered (MiB) | Deserialize (s) |
|---|---|---|---|---|---|---|---|
| inproc | 1 | 9.4 | 8.8 | 12 681 | same process | 0 | 0 |
| process | 2 | 5.2 | 3.8 | 6 452 | 807 | 360 | 0.00 |
| process | 4 | 5.2 | 4.0 | 4 515 | 807 | 360 | 0.00 |
| process | 8 | 3.8 | 2.3 | 2 568 | 807 | 360 | 0.00 |
| http | 2 | 5.2 | 3.9 | 6 464 | 818 | 360 | 0.00 |
| http | 4 | 5.0 | 3.7 | 4 545 | 823 | 360 | 0.00 |
| http | 8 | 4.4 | 3.0 | 2 456 | 827 | 360 | 0.00 |

### 5.2 One core for each process (`taskset`), the pod model

The baseline runs on one core (`taskset -c 0`). K workers run on K cores.
This is the shape of the Helm defaults: 1 CPU for each pod.

| Transport | K | Wall (s) | Speed-up | Slowest worker (s) | Peak RSS of a worker (MiB) | Peak RSS of the coordinator (MiB) | Bytes gathered (MiB) |
|---|---|---|---|---|---|---|---|
| inproc | 1 | 39.5 | 1.0x | 38.9 | 12 685 | same process | 0 |
| process | 2 | 11.0 | 3.6x | 9.8 | 6 455 | 808 | 360 |
| process | 4 | 7.5 | 5.2x | 6.2 | 4 480 | 808 | 360 |
| process | 8 | 5.9 | 6.7x | 4.3 | 2 449 | 808 | 360 |
| http | 4 | 7.7 | 5.1x | 6.1 | 4 544 | 822 | 360 |
| http | 8 | 6.2 | 6.4x | 4.4 | 2 463 | 828 | 360 |

The speed-up at K = 2 is above 2x. The one-core baseline runs 110 read
threads and a 12.7 GiB working set on one core; the split removes that
contention as well as dividing the work.

### 5.3 What the numbers say

- **Memory divides.** The worker peak goes from 12.7 GiB to 6.4, 4.5 and
  2.5 GiB for K = 2, 4, 8: about 0.8 GiB fixed plus the cube share. The
  coordinator holds 0.8 GiB: the output and the interpreter.
- **The baseline needs 2.8x the cube.** `numpy.ma.stack` copies the slices,
  and the reduce makes more copies. A streaming `max` would divide this by
  `t` with no network. This is a separate, cheap improvement.
- **Wall time divides less than K when the cores are shared.** One process
  already reads with 110 threads on this host. The gain in §5.1 comes from
  the single-threaded stack and reduce. §5.2 is the pod model: one core for
  each process. There the gain is 3.6x, 5.2x and 6.7x for K = 2, 4, 8. The
  curve flattens because the 360 MiB gather and the fixed start cost of each
  worker do not divide.
- **Time slices were not measured.** The spike splits space only. A split
  over `t` for `max` needs a second `max` on the coordinator and gives the
  same memory division; it is item 6 of §7.
- **The transfer is cheap.** 360 MiB come back in every case, because the
  output does not depend on K. Deserialization is zero-copy and takes no
  measurable time. The HTTP path costs about 0.5 s more than raw processes
  at K = 8 on loopback, for 360 MiB through uvicorn and httpx.
- **What did not work.** A grid split with `ceil(sqrt(K))` columns dropped
  one window at K = 8; the split now uses an exact factor pair. A FastAPI
  endpoint typed with a function-local alias under
  `from __future__ import annotations` failed at request time; the endpoint
  takes a plain `dict`.

## 6. Consequences

- A new request path (coordinator) and a new internal endpoint (chunk).
  Both are opt-in by settings; the default stays as today.
- The Helm chart gains an optional worker Deployment, Service and HPA, and
  settings for the threshold, the worker URL and the pool size.
- Cancellation (ADR 0011) must propagate to chunk requests.
- `max_pixels` becomes a limit for each chunk; a new total limit protects
  the coordinator.
- Observability: one request becomes K + 1 spans. The logs carry the
  parent request id.
- The process classes table (§2.2) is a new maintenance duty: a new
  process must declare its class.
- The GPU path adds an optional CuPy extra and the data-plus-mask pattern
  in the processes that use it. It does not change the CPU path.

## 7. Delivery

Each PR can be reviewed alone. Each one is optional after the first.

1. Process classes table and `GraphPartitioner` with the `local` executor
   (threads). No behavior change. Tests on the prefix computation.
2. Streaming `max`, `min` and the other array reducers over `t`, as
   `mean` does today (§5.3). Independent of distribution.
3. Chunk endpoint and the `http` executor with the guards of §4.4,
   behind a setting. The transport envelope (§4.3).
4. Helm: worker Deployment, Service, HPA and the settings.
5. `shared_memory` fast path for `WEB_CONCURRENCY > 1`.
6. Time-slice split for associative reducers.
7. Halo support for the neighbourhood class.
8. `queue` executor on pgqueuer, when batch jobs exist.
9. GPU worker pool and the Array API dispatch in `apply_kernel` and
   `fit_curve`, when a graph needs it.

## 8. Non-goals

- Dask, Ray, Spark, Beam, Legate, or any scheduler with a cluster state.
- Shuffles between workers. A worker returns one image to the coordinator.
- A distributed tile cache for XYZ services. Tile requests already divide
  the work; they gain from the worker pool, not from this ADR.
- A rewrite of `RasterStack` on xarray or Zarr.
- Buying GPU nodes for band math.
