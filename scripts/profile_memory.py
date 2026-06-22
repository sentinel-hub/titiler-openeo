#!/usr/bin/env python
"""Evaluate an openEO process graph standalone, for memory profiling.

This bypasses the HTTP layer so a single graph can be run under ``memray`` (or
with the built-in tracemalloc harness) without uvicorn/anyio threads in the way.
It is the "real #300 graph" runner referenced by EPIC #305 subtask 1.

Usage
-----
Enable the in-process harness (per-node heap deltas + retention summary)::

    TITILER_OPENEO_PROFILING_MEMORY=true \\
    TITILER_OPENEO_STAC_API_URL=https://stac.eoapi.dev \\
    uv run python scripts/profile_memory.py path/to/graph.json

Full native+heap flamegraph with memray::

    TITILER_OPENEO_STAC_API_URL=https://stac.eoapi.dev \\
    uv run memray run -o /tmp/openeo.bin scripts/profile_memory.py path/to/graph.json
    uv run memray flamegraph /tmp/openeo.bin        # -> /tmp/openeo-flamegraph.html

The graph JSON is either a bare ``{"process_graph": {...}}`` or a full process
definition (with ``parameters``/``id``), i.e. the same body ``POST /result``
accepts.
"""

import argparse
import json
import logging
import sys
from typing import Any, Dict

from openeo_pg_parser_networkx.graph import OpenEOProcessGraph
from openeo_pg_parser_networkx.process_registry import Process

from titiler.openeo.processes import PROCESS_SPECIFICATIONS, process_registry
from titiler.openeo.profiling import (
    memory_profiling_enabled,
    new_results_cache,
    profile_graph,
    report_retention,
)
from titiler.openeo.settings import BackendSettings
from titiler.openeo.stacapi import LoadCollection, LoadStac, stacApiBackend


def _build_registry() -> None:
    """Register the backend-specific loaders, mirroring main.py."""
    backend_settings = BackendSettings()  # type: ignore[call-arg]
    stac_client = stacApiBackend(
        str(backend_settings.stac_api_url),
        exclude_collections=backend_settings.exclude_collections,
    )  # type: ignore[call-arg]

    loaders = LoadCollection(stac_client)  # type: ignore[call-arg]
    process_registry["load_collection"] = Process(
        spec=PROCESS_SPECIFICATIONS["load_collection"],
        implementation=loaders.load_collection,
    )
    process_registry["load_stac"] = Process(
        spec=PROCESS_SPECIFICATIONS["load_stac"],
        implementation=LoadStac().load_stac,  # type: ignore[call-arg]
    )


def _resolve_parameters(process: Dict[str, Any]) -> Dict[str, Any]:
    """Apply process-definition defaults, mirroring the factory's /result path."""
    parameters: Dict[str, Any] = {}
    for param in process.get("parameters") or []:
        name = param.get("name")
        default = param.get("default")
        if name and default is not None:
            parameters[name] = default
    return parameters


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("graph", help="Path to a process-graph / process JSON file")
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Show debug-level logs"
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    if not memory_profiling_enabled():
        logging.warning(
            "TITILER_OPENEO_PROFILING_MEMORY is not set: the in-process harness "
            "will be silent. Set it for per-node logs, or run under `memray run`."
        )

    with open(args.graph) as fh:
        process = json.load(fh)

    # Accept either a full process definition or a bare process graph.
    if "process_graph" not in process:
        process = {"process_graph": process}

    _build_registry()

    parameters = _resolve_parameters(process)
    parsed_graph = OpenEOProcessGraph(pg_data=process)
    results_cache = new_results_cache()
    pg_callable = parsed_graph.to_callable(
        process_registry=process_registry,
        parameters=process.get("parameters"),
        results_cache=results_cache,
    )

    with profile_graph(f"profile {args.graph}"):
        result = pg_callable(named_parameters=parameters)
    report_retention(results_cache, args.graph)

    size = len(result.data) if hasattr(result, "data") else len(str(result))
    logging.info("Done: result=%s (~%d bytes)", type(result).__name__, size)
    return 0


if __name__ == "__main__":
    sys.exit(main())
