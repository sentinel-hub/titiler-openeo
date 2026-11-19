#!/usr/bin/env python
"""Spike for ADR 0015: scatter one process graph over spatial windows.

The spike answers two questions with numbers:

1. Does running the same sub-graph on K spatial windows in K processes cut
   wall time and the peak memory of each process?
2. What does it cost to move the window results back (bytes, seconds)?

It uses the real process registry and the real ``node_callable`` of
``openeo_pg_parser_networkx``, as ``scripts/debug_graph.py`` does. The data
is a stack of local synthetic COGs, so no STAC API and no network are needed.
The graph is ``reduce_dimension(max)`` over time, the process that stacks the
whole cube in memory (``math.py``, ``max`` on a ``RasterStack``).

Three transports run the same work:

- ``inproc``:  the baseline, one process, one window.
- ``process``: K worker processes (``multiprocessing``, spawn). Results come
  back through pickle protocol 5 with out-of-band buffers.
- ``http``:    K uvicorn workers on localhost; the coordinator POSTs a window
  and receives the pickled result. This is the "self-as-worker" fan-out.

Usage
-----
    uv run python scripts/spike_distributed/fanout_spike.py make-data \\
        --out /tmp/spike --dates 12 --size 4096
    uv run python scripts/spike_distributed/fanout_spike.py run \\
        --data /tmp/spike --transport inproc
    uv run python scripts/spike_distributed/fanout_spike.py run \\
        --data /tmp/spike --transport process --workers 4
    uv run python scripts/spike_distributed/fanout_spike.py run \\
        --data /tmp/spike --transport http --workers 4
    uv run python scripts/spike_distributed/fanout_spike.py worker --port 9001
"""

from __future__ import annotations

import argparse
import glob
import io
import json
import multiprocessing as mp
import os
import pickle
import resource
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple

import numpy as np

BBox = Tuple[float, float, float, float]

# --------------------------------------------------------------------------
# Synthetic data
# --------------------------------------------------------------------------


def make_data(out: str, dates: int, size: int, bands: int, seed: int) -> None:
    """Write ``dates`` float32 COGs of ``size`` x ``size`` pixels in EPSG:32631.

    Each COG has a different nodata hole, so the mask matters for ``max``.
    """
    import rasterio
    from rasterio.transform import from_origin

    os.makedirs(out, exist_ok=True)
    rng = np.random.default_rng(seed)
    res = 10.0
    transform = from_origin(500_000.0, 5_000_000.0, res, res)
    profile = {
        "driver": "COG",
        "dtype": "float32",
        "count": bands,
        "width": size,
        "height": size,
        "crs": "EPSG:32631",
        "transform": transform,
        "nodata": -9999.0,
        "compress": "DEFLATE",
        "blocksize": 512,
    }
    for i in range(dates):
        arr = rng.random((bands, size, size), dtype=np.float32) * 100.0
        # A nodata stripe that moves with the date.
        y0 = (i * size) // dates
        arr[:, y0 : y0 + size // dates, :] = -9999.0
        path = os.path.join(out, f"scene_{i:02d}.tif")
        with rasterio.open(path, "w", **profile) as dst:
            dst.write(arr)
        print(f"wrote {path}", file=sys.stderr)


# --------------------------------------------------------------------------
# One window of the graph
# --------------------------------------------------------------------------

GRAPH = {
    "process_graph": {
        "reduce": {
            "process_id": "reduce_dimension",
            "arguments": {
                "data": {"from_parameter": "data"},
                "dimension": "t",
                "reducer": {
                    "process_graph": {
                        "max": {
                            "process_id": "max",
                            "arguments": {"data": {"from_parameter": "data"}},
                            "result": True,
                        }
                    }
                },
            },
            "result": True,
        }
    }
}


def scene_paths(data_dir: str) -> List[str]:
    paths = sorted(glob.glob(os.path.join(data_dir, "scene_*.tif")))
    if not paths:
        raise SystemExit(f"no scene_*.tif in {data_dir}; run make-data first")
    return paths


def full_extent(data_dir: str) -> Tuple[BBox, Any, int, int]:
    import rasterio

    with rasterio.open(scene_paths(data_dir)[0]) as src:
        return tuple(src.bounds), src.crs, src.width, src.height  # type: ignore


def split_windows(
    bounds: BBox, width: int, height: int, k: int
) -> List[Dict[str, Any]]:
    """Split the extent into ``k`` windows on a near-square grid.

    Returns pixel offsets and bounds for each window, so the coordinator can
    place the results without a mosaic.
    """
    # Exact factor pair closest to a square, so every pixel lands in one window.
    cols = next(c for c in range(int(np.sqrt(k)), 0, -1) if k % c == 0)
    rows = k // cols
    west, south, east, north = bounds
    xres = (east - west) / width
    yres = (north - south) / height
    xs = np.linspace(0, width, cols + 1, dtype=int)
    ys = np.linspace(0, height, rows + 1, dtype=int)
    windows: List[Dict[str, Any]] = []
    for r in range(rows):
        for c in range(cols):
            if len(windows) == k:
                break
            x0, x1, y0, y1 = xs[c], xs[c + 1], ys[r], ys[r + 1]
            windows.append(
                {
                    "col_off": int(x0),
                    "row_off": int(y0),
                    "width": int(x1 - x0),
                    "height": int(y1 - y0),
                    "bounds": (
                        west + x0 * xres,
                        north - y1 * yres,
                        west + x1 * xres,
                        north - y0 * yres,
                    ),
                }
            )
    return windows


def run_window(data_dir: str, window: Dict[str, Any]) -> Dict[str, Any]:
    """Run the graph on one window and return the result and process metrics.

    This runs inside the worker (process or HTTP). Nothing here knows about
    the other windows: this is the chunk-safe property of the graph.
    """
    from openeo_pg_parser_networkx.graph import OpenEOProcessGraph
    from rio_tiler.io import Reader

    from titiler.openeo.processes import process_registry
    from titiler.openeo.processes.implementations.data_model import RasterStack

    t0 = time.perf_counter()
    bounds = tuple(window["bounds"])
    width, height = window["width"], window["height"]
    paths = scene_paths(data_dir)

    def make_task(path: str, i: int):
        def task():
            with Reader(path) as src:
                return src.part(bounds, bounds_crs=src.crs, width=width, height=height)

        meta = {"datetime": datetime(2024, 1, 1 + i, tzinfo=timezone.utc)}
        return task, meta

    tasks = [make_task(p, i) for i, p in enumerate(paths)]
    with Reader(paths[0]) as src:
        crs = src.crs
    stack = RasterStack(
        tasks,
        timestamp_fn=lambda meta: meta["datetime"],
        width=width,
        height=height,
        bounds=bounds,
        dst_crs=crs,
    )

    callable_ = OpenEOProcessGraph(pg_data=GRAPH).to_callable(
        process_registry=process_registry
    )
    result = callable_(named_parameters={"data": stack})
    img = result if hasattr(result, "array") else next(iter(result.values()))
    arr = np.ma.asanyarray(img.array)
    elapsed = time.perf_counter() - t0
    maxrss_mib = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    return {
        "data": np.ascontiguousarray(arr.filled(np.nan)),
        "mask": np.ascontiguousarray(np.ma.getmaskarray(arr)),
        "window": window,
        "seconds": elapsed,
        "maxrss_mib": maxrss_mib,
        "pid": os.getpid(),
    }


# --------------------------------------------------------------------------
# Transport: pickle protocol 5 with out-of-band buffers
# --------------------------------------------------------------------------


def dumps(obj: Any) -> bytes:
    buffers: List[pickle.PickleBuffer] = []
    head = pickle.dumps(obj, protocol=5, buffer_callback=buffers.append)
    out = io.BytesIO()
    out.write(len(head).to_bytes(8, "big"))
    out.write(head)
    for b in buffers:
        raw = b.raw()
        out.write(raw.nbytes.to_bytes(8, "big"))
        out.write(raw)
    return out.getvalue()


def loads(blob: bytes) -> Any:
    view = memoryview(blob)
    n = int.from_bytes(view[:8], "big")
    head = view[8 : 8 + n]
    pos = 8 + n
    buffers = []
    while pos < len(view):
        m = int.from_bytes(view[pos : pos + 8], "big")
        buffers.append(view[pos + 8 : pos + 8 + m])
        pos += 8 + m
    return pickle.loads(head, buffers=buffers)


# --------------------------------------------------------------------------
# Workers
# --------------------------------------------------------------------------


def _process_worker(args: Tuple[str, Dict[str, Any]]) -> bytes:
    data_dir, window = args
    return dumps(run_window(data_dir, window))


def build_http_app():
    from anyio import to_thread
    from fastapi import Body, FastAPI, Response

    app = FastAPI()

    @app.post("/window")
    async def window_endpoint(body: dict = Body(...)) -> Response:
        result = await to_thread.run_sync(run_window, body["data_dir"], body["window"])
        return Response(content=dumps(result), media_type="application/octet-stream")

    return app


def serve_worker(port: int) -> None:
    import uvicorn

    uvicorn.run(build_http_app(), host="127.0.0.1", port=port, log_level="warning")


def start_http_workers(k: int, base_port: int) -> List[subprocess.Popen]:
    import httpx

    procs = []
    for i in range(k):
        port = base_port + i
        procs.append(
            subprocess.Popen(
                [sys.executable, __file__, "worker", "--port", str(port)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        )
    deadline = time.time() + 60
    for i in range(k):
        url = f"http://127.0.0.1:{base_port + i}/docs"
        while True:
            try:
                httpx.get(url, timeout=1.0)
                break
            except httpx.HTTPError:
                if time.time() > deadline:
                    raise SystemExit("workers did not start") from None
                time.sleep(0.2)
    return procs


# --------------------------------------------------------------------------
# Coordinator
# --------------------------------------------------------------------------


def gather(
    results: List[Dict[str, Any]], width: int, height: int, bands: int
) -> np.ma.MaskedArray:
    """Place the window results into the full grid. No resampling, no mosaic."""
    data = np.full((bands, height, width), np.nan, dtype=np.float32)
    mask = np.ones((bands, height, width), dtype=bool)
    for r in results:
        w = r["window"]
        sl = (
            slice(None),
            slice(w["row_off"], w["row_off"] + w["height"]),
            slice(w["col_off"], w["col_off"] + w["width"]),
        )
        data[sl] = r["data"]
        mask[sl] = r["mask"]
    return np.ma.MaskedArray(data, mask=mask)


def run(args: argparse.Namespace) -> None:
    bounds, _crs, width, height = full_extent(args.data)
    with __import__("rasterio").open(scene_paths(args.data)[0]) as src:
        bands = src.count
    k = 1 if args.transport == "inproc" else args.workers
    windows = split_windows(bounds, width, height, k)

    t0 = time.perf_counter()
    blobs: List[bytes] = []
    results: List[Dict[str, Any]] = []
    transfer_bytes = 0
    t_transfer = 0.0

    if args.transport == "inproc":
        results = [run_window(args.data, windows[0])]
    elif args.transport == "process":
        ctx = mp.get_context("spawn")
        with ctx.Pool(k) as pool:
            blobs = pool.map(_process_worker, [(args.data, w) for w in windows])
    elif args.transport == "http":
        import httpx

        procs = start_http_workers(k, args.base_port)
        try:
            t_start = time.perf_counter()

            def post(i_w):
                i, w = i_w
                url = f"http://127.0.0.1:{args.base_port + i}/window"
                r = httpx.post(
                    url, json={"data_dir": args.data, "window": w}, timeout=600.0
                )
                r.raise_for_status()
                return r.content

            with ThreadPoolExecutor(k) as ex:
                blobs = list(ex.map(post, enumerate(windows)))
            t_transfer = time.perf_counter() - t_start
        finally:
            for p in procs:
                p.terminate()
    else:
        raise SystemExit(f"unknown transport {args.transport}")

    if blobs:
        t1 = time.perf_counter()
        results = [loads(b) for b in blobs]
        t_deser = time.perf_counter() - t1
        transfer_bytes = sum(len(b) for b in blobs)
    else:
        t_deser = 0.0

    merged = gather(results, width, height, bands)
    wall = time.perf_counter() - t0

    out = {
        "transport": args.transport,
        "workers": k,
        "grid": f"{width}x{height}x{bands}",
        "dates": len(scene_paths(args.data)),
        "wall_s": round(wall, 2),
        "worker_s_max": round(max(r["seconds"] for r in results), 2),
        "worker_maxrss_mib_max": round(max(r["maxrss_mib"] for r in results)),
        "coordinator_maxrss_mib": round(
            resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
        ),
        "transfer_mib": round(transfer_bytes / 2**20, 1),
        "deserialize_s": round(t_deser, 3),
        "http_roundtrip_s": round(t_transfer, 2) if args.transport == "http" else None,
        "valid_pixels": int((~merged.mask).sum()),
        "checksum": float(np.nansum(merged.filled(0.0).astype(np.float64))),
    }
    print(json.dumps(out))
    if args.save:
        np.save(args.save, merged.filled(np.nan))
        np.save(args.save.replace(".npy", "_mask.npy"), np.ma.getmaskarray(merged))
    if args.compare:
        ref = np.load(args.compare)
        ref_mask = np.load(args.compare.replace(".npy", "_mask.npy"))
        same = np.array_equal(
            merged.filled(np.nan), ref, equal_nan=True
        ) and np.array_equal(np.ma.getmaskarray(merged), ref_mask)
        print(json.dumps({"equal_to_baseline": bool(same)}))
        if not same:
            raise SystemExit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("make-data", help="write synthetic COGs")
    p.add_argument("--out", required=True)
    p.add_argument("--dates", type=int, default=12)
    p.add_argument("--size", type=int, default=4096)
    p.add_argument("--bands", type=int, default=1)
    p.add_argument("--seed", type=int, default=0)

    p = sub.add_parser("run", help="run the graph with one transport")
    p.add_argument("--data", required=True)
    p.add_argument(
        "--transport", choices=["inproc", "process", "http"], default="inproc"
    )
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--base-port", type=int, default=9001)
    p.add_argument("--save", help="save the merged result (.npy) for --compare")
    p.add_argument("--compare", help="baseline .npy to compare with")

    p = sub.add_parser("worker", help="serve one HTTP worker")
    p.add_argument("--port", type=int, required=True)

    args = parser.parse_args()
    if args.cmd == "make-data":
        make_data(args.out, args.dates, args.size, args.bands, args.seed)
    elif args.cmd == "run":
        run(args)
    elif args.cmd == "worker":
        serve_worker(args.port)


if __name__ == "__main__":
    main()
