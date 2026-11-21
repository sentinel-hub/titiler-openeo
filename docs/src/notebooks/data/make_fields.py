"""Make the demo field files of the vector notebooks.

`mol_fields.geojson` is the source: the 4 fields of the openEO Python client
tutorial, near Mol (Belgium). This script writes the same fields as
FlatGeobuf and GeoParquet, with the output encoders of titiler-openeo, for
`load_url` in the notebooks.

Run it from the repository root, with the `vector` extra:

    uv run python docs/src/notebooks/data/make_fields.py
"""

import json
from pathlib import Path

from titiler.openeo.processes.implementations.formats import encode
from titiler.openeo.processes.implementations.vector_model import VectorCube

HERE = Path(__file__).parent


def main() -> None:
    """Write mol_fields.fgb and mol_fields.parquet next to the GeoJSON."""
    fields = json.loads((HERE / "mol_fields.geojson").read_text())
    cube = VectorCube.from_geojson(fields)
    for format_name, extension in (("FlatGeobuf", "fgb"), ("GeoParquet", "parquet")):
        result = encode(cube, format_name, {})
        target = HERE / f"mol_fields.{extension}"
        target.write_bytes(result.data)
        print(f"{target.relative_to(Path.cwd())}: {len(result.data)} bytes")


if __name__ == "__main__":
    main()
