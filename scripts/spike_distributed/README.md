# Spike: scatter one graph over spatial windows (ADR 0015)

`fanout_spike.py` runs the real process registry and the real
`node_callable` on local synthetic COGs. It compares one process with K
worker processes and with K HTTP workers. See
[ADR 0015](../../docs/adr/0015-distributed-graph-execution.md) §5 for the
numbers and what they say.

The script has no dependency outside the project environment.

## Commands

```bash
SP=/tmp/spike

# 12 dates, 2 bands, 6144 x 6144 float32: a 3.6 GB cube (3.7 GB on disk)
uv run python scripts/spike_distributed/fanout_spike.py make-data \
    --out $SP/data --dates 12 --size 6144 --bands 2

# Baseline: one process, one window. Saves the result for the comparisons.
uv run python scripts/spike_distributed/fanout_spike.py run \
    --data $SP/data --transport inproc --save $SP/base.npy

# K worker processes (pickle 5, out-of-band buffers)
uv run python scripts/spike_distributed/fanout_spike.py run \
    --data $SP/data --transport process --workers 4 --compare $SP/base.npy

# K uvicorn workers on localhost, httpx fan-out
uv run python scripts/spike_distributed/fanout_spike.py run \
    --data $SP/data --transport http --workers 4 --compare $SP/base.npy
```

The pod model of ADR 0015 §5.2 pins the baseline to one core and K workers
to K cores:

```bash
taskset -c 0   uv run python scripts/spike_distributed/fanout_spike.py run --data $SP/data --transport inproc
taskset -c 0-3 uv run python scripts/spike_distributed/fanout_spike.py run --data $SP/data --transport process --workers 4
```

Each `run` prints one JSON line: wall time, the slowest worker, the peak RSS
of a worker and of the coordinator, the bytes gathered and the deserialize
time. With `--compare` it also prints `equal_to_baseline` and exits with 1
when the data or the mask differ.

## What the script does

1. `make-data` writes `scene_NN.tif` COGs in EPSG:32631 with a nodata
   stripe that moves with the date, so the mask matters for `max`.
2. `run` splits the full extent into K windows on an exact factor grid,
   so each pixel lands in one window.
3. Each worker builds a lazy `RasterStack` (one `Reader.part` task for each
   date) and runs the graph `reduce_dimension(max, t)` through
   `OpenEOProcessGraph(...).to_callable(process_registry)`.
4. The result travels as pickle protocol 5 with out-of-band buffers. The
   coordinator rebuilds the arrays over the received bytes without a copy
   and places each window in the output grid.
