#!/usr/bin/env python
"""Run an openEO process graph locally for debugging.

This compiles a process graph the SAME way the server does
(``OpenEOProcessGraph(...).to_callable(process_registry=...)``) and runs it
in-process — no FastAPI, no kubernetes. It exercises the real
``openeo_pg_parser_networkx`` ``node_callable`` (shared ``results_cache``,
nested-callback scope resolution, …), which plain-Python unit tests bypass and
which is where most apply/array_apply/reduce bugs live.

Two modes
=========

1. FULL graph against the real backend (load_collection / load_stac).
   Set the same env vars the app needs, then point at the graph JSON:

       export TITILER_OPENEO_STAC_API_URL=https://stac.eoapi.dev
       export TITILER_OPENEO_STORE_URL=file:///tmp/store        # or a DSN
       # credentials for reading the collection's assets (rio-tiler/GDAL):
       export AWS_ACCESS_KEY_ID=...  AWS_SECRET_ACCESS_KEY=...  AWS_REGION=...
       export AWS_S3_ENDPOINT=...                 # non-AWS S3 (e.g. CDSE)
       # or: export AWS_NO_SIGN_REQUEST=YES       # public buckets
       # or: GDAL_HTTP_*, EARTHDATA_* … whatever the assets require

       uv run python scripts/debug_graph.py path/to/graph.json

   ``load_collection`` is registered only when TITILER_OPENEO_STAC_API_URL is set.

2. ISOLATED sub-graph (no backend): inject the inputs a failing inner node needs
   via ``--params``. This is the fastest way to reproduce an apply_dimension /
   array_apply / reduce_dimension bug with a tiny synthetic RasterStack.

       uv run python scripts/debug_graph.py subgraph.json --params params.py

   ``--params`` accepts:
     * a ``.json`` file  -> parsed and passed as named_parameters (scalars only), or
     * a ``.py``  file  -> executed; its module-level ``named_parameters`` dict is
       used. Use this to build a synthetic ``RasterStack`` (see the example printed
       by ``--print-example``), since a datacube can't be expressed in JSON.

Examples
--------
    uv run python scripts/debug_graph.py --print-example          # writes a sample params.py to stdout
    uv run python scripts/debug_graph.py mygraph.json             # full graph (needs backend env)
    uv run python scripts/debug_graph.py subgraph.json -p params.py
"""

from __future__ import annotations

import argparse
import json
import os
import runpy
import sys
from typing import Any, Dict

from openeo_pg_parser_networkx.graph import OpenEOProcessGraph
from openeo_pg_parser_networkx.process_registry import Process

from titiler.openeo.processes import PROCESS_SPECIFICATIONS, process_registry

EXAMPLE_PARAMS = '''\
"""Example params.py for scripts/debug_graph.py (isolated sub-graph mode).

Build whatever the graph references via {"from_parameter": "<name>"} and expose
them as a module-level ``named_parameters`` dict.
"""
from datetime import datetime

import numpy as np
from rio_tiler.models import ImageData

from titiler.openeo.processes.implementations.data_model import RasterStack

# A tiny 3-timestamp, single-band temporal stack (values 3, 7, 5).
data = RasterStack.from_images(
    {
        datetime(2024, 6, 1): ImageData(np.ma.array(np.full((1, 2, 2), 3.0))),
        datetime(2024, 6, 2): ImageData(np.ma.array(np.full((1, 2, 2), 7.0))),
        datetime(2024, 6, 3): ImageData(np.ma.array(np.full((1, 2, 2), 5.0))),
    }
)

named_parameters = {"data": data}
'''


def maybe_register_loaders() -> None:
    """Register load_collection/load_stac iff backend env is configured.

    Mirrors titiler.openeo.main so FULL graphs run, but stays optional so
    sub-graph debugging needs no backend.
    """
    if not os.environ.get("TITILER_OPENEO_STAC_API_URL"):
        print(
            "[debug_graph] TITILER_OPENEO_STAC_API_URL unset -> load_collection NOT "
            "registered (sub-graph mode).",
            file=sys.stderr,
        )
        return

    from titiler.openeo.settings import BackendSettings
    from titiler.openeo.stacapi import LoadCollection, LoadStac, stacApiBackend

    settings = BackendSettings()  # type: ignore[call-arg]
    client = stacApiBackend(
        str(settings.stac_api_url),
        exclude_collections=settings.exclude_collections,
    )
    process_registry["load_collection"] = Process(
        spec=PROCESS_SPECIFICATIONS["load_collection"],
        implementation=LoadCollection(client).load_collection,
    )
    process_registry["load_stac"] = Process(
        spec=PROCESS_SPECIFICATIONS["load_stac"],
        implementation=LoadStac().load_stac,
    )
    print(
        f"[debug_graph] load_collection registered against {settings.stac_api_url}",
        file=sys.stderr,
    )


def load_named_parameters(path: str) -> Dict[str, Any]:
    if path.endswith(".py"):
        ns = runpy.run_path(path)
        if "named_parameters" not in ns:
            raise SystemExit(
                f"{path} must define a module-level `named_parameters` dict"
            )
        return ns["named_parameters"]
    with open(path) as f:
        return json.load(f)


def describe(result: Any) -> str:
    """Human-readable summary of a graph result (RasterStack / ImageData / array)."""
    import numpy as np

    if hasattr(result, "items") and not isinstance(result, dict):
        result = dict(result.items())

    def _stats(arr: Any) -> str:
        arr = np.ma.asanyarray(arr)
        finite = arr.compressed() if np.ma.isMaskedArray(arr) else arr.ravel()
        finite = finite[np.isfinite(finite)]
        masked_pct = 100.0 * (1 - finite.size / max(arr.size, 1))
        if finite.size == 0:
            return f"shape={arr.shape} dtype={arr.dtype} ALL masked/nan"
        return (
            f"shape={arr.shape} dtype={arr.dtype} "
            f"min={finite.min():.4g} max={finite.max():.4g} "
            f"mean={finite.mean():.4g} masked/nan={masked_pct:.0f}%"
        )

    if isinstance(result, dict):
        lines = [f"RasterStack with {len(result)} item(s):"]
        for key, img in result.items():
            lines.append(f"  {key}: {_stats(getattr(img, 'array', img))}")
        return "\n".join(lines)
    if hasattr(result, "array"):
        return f"ImageData {_stats(result.array)}"
    return f"{type(result).__name__} {_stats(result)}"


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "graph", nargs="?", help="process-graph JSON (body or full openEO doc)"
    )
    parser.add_argument(
        "-p", "--params", help="named_parameters as a .json or .py file"
    )
    parser.add_argument(
        "--print-example",
        action="store_true",
        help="print an example params.py and exit",
    )
    args = parser.parse_args()

    if args.print_example:
        print(EXAMPLE_PARAMS)
        return
    if not args.graph:
        parser.error("graph is required (or use --print-example)")

    with open(args.graph) as f:
        doc = json.load(f)
    pg = doc if "process_graph" in doc else {"process_graph": doc}

    named_parameters = load_named_parameters(args.params) if args.params else {}

    maybe_register_loaders()

    callable_ = OpenEOProcessGraph(pg_data=pg).to_callable(
        process_registry=process_registry
    )
    result = callable_(named_parameters=named_parameters)
    print(describe(result))


if __name__ == "__main__":
    main()
