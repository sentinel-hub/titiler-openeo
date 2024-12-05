"""titiler.openeo.processes."""

import json
from copy import copy
from pathlib import Path
from typing import Dict, List

from attr import define, field

json_path = Path(__file__).parent / "data"
process_json_paths = [pg_path for pg_path in (json_path).glob("*.json")]  #  noqa: C416
DEFAULT_PROCESSES = {f.stem: json.load(open(f)) for f in process_json_paths}


@define(frozen=True)
class Processes:
    """Algorithms."""

    data: Dict[str, Dict] = field()

    def get(self, name: str) -> Dict:
        """Fetch a TMS."""
        if name not in self.data:
            raise KeyError(f"Invalid name: {name}")

        return self.data[name]

    def list(self) -> List[str]:
        """List registered Algorithm."""
        return list(self.data.keys())

    def register(
        self,
        processes: Dict[str, Dict],
        overwrite: bool = False,
    ) -> "Processes":
        """Register Process(es)."""
        for name in processes:
            if name in self.data and not overwrite:
                raise Exception(f"{name} is already a registered. Use overwrite=True.")

        return Processes({**self.data, **processes})  # type: ignore


processes = Processes(copy(DEFAULT_PROCESSES))  # type: ignore
