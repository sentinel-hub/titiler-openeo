"""titiler.openeo.processes."""

import json
from pathlib import Path

from openeo_pg_parser_networkx import ProcessRegistry
from openeo_pg_parser_networkx.process_registry import Process

from .implementations import (  # apply_pixel_selection,
    clip,
    linear_scale_range,
    load_collection,
    load_collection_and_reduce,
    normalized_difference,
    save_result,
)
from .implementations.core import process

json_path = Path(__file__).parent / "specs"
process_json_paths = [pg_path for pg_path in (json_path).glob("*.json")]  #  noqa: C416
DEFAULT_PROCESSES = {f.stem: json.load(open(f)) for f in process_json_paths}

# `process` is wrapped around each registered implementation
process_registry = ProcessRegistry(wrap_funcs=[process])
process_registry["normalized_difference"] = Process(
    spec=DEFAULT_PROCESSES["normalized_difference"],
    implementation=normalized_difference,
)
process_registry["clip"] = process_registry["clip"] = Process(
    spec=DEFAULT_PROCESSES["clip"], implementation=clip
)
process_registry["linear_scale_range"] = process_registry["linear_scale_range"] = (
    Process(
        spec=DEFAULT_PROCESSES["linear_scale_range"], implementation=linear_scale_range
    )
)
process_registry["load_collection"] = process_registry["load_collection"] = Process(
    spec=DEFAULT_PROCESSES["load_collection"], implementation=load_collection
)
process_registry["load_collection_and_reduce"] = process_registry[
    "load_collection_and_reduce"
] = Process(
    spec=DEFAULT_PROCESSES["load_collection_and_reduce"],
    implementation=load_collection_and_reduce,
)
# process_registry["apply_pixel_selection"] = process_registry["apply_pixel_selection"] = Process(
#     spec=DEFAULT_PROCESSES["apply_pixel_selection"], implementation=apply_pixel_selection
# )
process_registry["save_result"] = process_registry["save_result"] = Process(
    spec=DEFAULT_PROCESSES["save_result"], implementation=save_result
)


#  Import these pre-defined processes from openeo_processes_dask and register them into registry
# processes_from_module = [
#     func
#     for _, func in inspect.getmembers(
#         importlib.import_module("openeo_processes_dask.process_implementations"),
#         inspect.isfunction,
#     )
# ]

# specs_module = importlib.import_module("openeo_processes_dask.specs")
# specs = {
#     func.__name__: getattr(specs_module, func.__name__)
#     for func in processes_from_module
# }

# for func in processes_from_module:
#     process_registry[func.__name__] = Process(
#         spec=specs[func.__name__], implementation=func
#     )
