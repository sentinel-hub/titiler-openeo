"""titiler.openeo.processes."""

import json
from pathlib import Path

from openeo_pg_parser_networkx import ProcessRegistry
from openeo_pg_parser_networkx.process_registry import Process

from .implementations import (
    clip,
    linear_scale_range,
    normalized_difference,
    save_result,
)
from .implementations.core import process

json_path = Path(__file__).parent / "specs"
process_json_paths = [pg_path for pg_path in (json_path).glob("*.json")]  #  noqa: C416
PROCESS_SPECIFICATIONS = {f.stem: json.load(open(f)) for f in process_json_paths}

# `process` is wrapped around each registered implementation
process_registry = ProcessRegistry(wrap_funcs=[process])
process_registry["normalized_difference"] = Process(
    spec=PROCESS_SPECIFICATIONS["normalized_difference"],
    implementation=normalized_difference,
)
process_registry["clip"] = process_registry["clip"] = Process(
    spec=PROCESS_SPECIFICATIONS["clip"], implementation=clip
)
process_registry["linear_scale_range"] = process_registry["linear_scale_range"] = (
    Process(
        spec=PROCESS_SPECIFICATIONS["linear_scale_range"],
        implementation=linear_scale_range,
    )
)
process_registry["save_result"] = process_registry["save_result"] = Process(
    spec=PROCESS_SPECIFICATIONS["save_result"], implementation=save_result
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
