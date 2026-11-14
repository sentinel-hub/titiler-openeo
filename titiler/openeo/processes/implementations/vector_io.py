"""Vector readers for `load_url` (ADR 0014 §2.5).

Each reader takes the URL of the user, the URL to read (signed, if the
deployment has a signer) and the format options, and returns a `VectorCube`.
The geometries keep the CRS of the file, and the attributes of the file
become the properties of each geometry.

The readers of the `vector` extra import pyogrio, shapely and arro3 inside the
function. This module is not star-imported into `implementations`: its
functions are not openEO processes.
"""

import json
import math
import urllib.error
import urllib.request
import warnings
from typing import Any, Dict, List, Optional, Sequence
from urllib.parse import urlparse

from rasterio.crs import CRS
from rio_tiler.constants import WGS84_CRS

from ...errors import (
    FormatUnsuitable,
    ProcessParameterInvalid,
    VectorFeatureLimitExceeded,
)
from ...settings import ProcessingSettings
from .vector_model import VectorCube

_PROCESS = "load_url"
_DOWNLOAD_TIMEOUT = 60  # seconds, for each socket operation
_PARQUET_MAGIC = b"PAR1"


def check_url(url: str) -> None:
    """Accept HTTP and HTTPS URLs only.

    `urllib` also opens `file://` and `ftp://` URLs, and GDAL `/vsicurl/` gives
    the URL to libcurl, which knows more schemes. The spec of `load_url` allows
    HTTP and HTTPS only.
    """
    if urlparse(url).scheme.lower() not in ("http", "https"):
        raise ProcessParameterInvalid(
            "load_url reads vector files from HTTP and HTTPS URLs only."
        )


def _public(error: Exception, url: str, href: str) -> str:
    """Return the error message, without the signed URL (it can hold a secret)."""
    message = str(error)
    return message.replace(href, url) if href != url else message


def _check_count(count: int) -> None:
    limit = ProcessingSettings().max_vector_features
    if count > limit:
        raise VectorFeatureLimitExceeded(_PROCESS, limit)


def _download(url: str, href: str) -> bytes:
    """Return the bytes at `href`, up to `max_vector_download_bytes`."""
    limit = ProcessingSettings().max_vector_download_bytes
    try:
        # The scheme is checked by `check_url`.
        with urllib.request.urlopen(href, timeout=_DOWNLOAD_TIMEOUT) as response:  # noqa: S310
            data = response.read(limit + 1)
    except (urllib.error.URLError, OSError) as error:  # HTTPError is a URLError
        raise ProcessParameterInvalid(
            f"load_url can't download {url}: {_public(error, url, href)}"
        ) from None
    if len(data) > limit:
        raise ProcessParameterInvalid(
            f"The file at {url} is larger than {limit} bytes, the limit of this "
            "back-end for a vector file."
        )
    return data


def _cell(value: Any) -> Any:
    """Return a property value. A float NaN (no data in OGR) becomes None."""
    if isinstance(value, float) and math.isnan(value):
        return None
    return value


def _rows(names: Sequence[str], columns: Sequence[List[Any]], count: int) -> List[Dict]:
    """Return one properties dict for each row of the columns."""
    return [
        {name: _cell(column[i]) for name, column in zip(names, columns)}
        for i in range(count)
    ]


def _geometries(wkb: Sequence[Optional[bytes]]) -> List[Optional[Dict[str, Any]]]:
    """Return WKB geometries as GeoJSON geometry dicts (None stays None)."""
    import shapely
    from shapely.geometry import mapping

    return [
        None if geometry is None else mapping(geometry)
        for geometry in shapely.from_wkb(list(wkb))
    ]


def read_geojson(url: str, href: str, options: Dict) -> VectorCube:
    """Read a GeoJSON file (RFC 7946, so the coordinates are in EPSG:4326)."""
    data = _download(url, href)
    try:
        cube = VectorCube.from_geojson(json.loads(data))
    except ValueError as error:  # json.JSONDecodeError is a ValueError
        raise FormatUnsuitable(
            f"load_url can't read {url} as GeoJSON: {error}"
        ) from None
    _check_count(len(cube))
    return cube


def _vsi_path(url: str, href: str) -> str:
    """Return the GDAL path of the URL. A zip archive is opened as a folder."""
    path = f"/vsicurl/{href}"
    if urlparse(url).path.lower().endswith(".zip"):
        path = f"/vsizip/{path}"
    return path


def _ogr_reader(format_name: str, layer_option: Optional[str] = None):
    """Return a reader for an OGR format (pyogrio)."""

    def read(url: str, href: str, options: Dict) -> VectorCube:
        import pyogrio.raw
        from pyogrio.errors import DataLayerError, DataSourceError

        # GDAL warns when the path of a GeoPackage does not end with `.gpkg`,
        # for example a signed URL with a query string. GDAL reads the file
        # correctly, and the warning would write the signed URL (and its
        # token) to the logs. `filterwarnings` replaces an equal filter, so
        # a call for each read does not add filters.
        warnings.filterwarnings(
            "ignore",
            message=r".*has GPKG application_id, but non conformant file extension",
            category=RuntimeWarning,
        )
        limit = ProcessingSettings().max_vector_features
        try:
            meta, _, wkb, fields = pyogrio.raw.read(
                _vsi_path(url, href),
                layer=options.get(layer_option) if layer_option else None,
                encoding=options.get("encoding"),
                max_features=limit + 1,
            )
        except (DataSourceError, DataLayerError) as error:
            raise FormatUnsuitable(
                f"load_url can't read {url} as {format_name}: "
                f"{_public(error, url, href)}"
            ) from None
        _check_count(len(wkb))

        # `tolist` gives Python values: datetime for a date, None for NaT.
        names = list(meta["fields"])
        columns = [array.tolist() for array in fields]
        return VectorCube(
            geometries=_geometries(wkb),
            crs=CRS.from_user_input(meta["crs"]) if meta.get("crs") else WGS84_CRS,
            properties=_rows(names, columns, len(wkb)),
        )

    return read


read_gpkg = _ogr_reader("GPKG", layer_option="layer_name")
read_flatgeobuf = _ogr_reader("FlatGeobuf")
read_shapefile = _ogr_reader("ESRI Shapefile")


def _read_parquet_table(data: bytes, url: str):
    """Return the Parquet file as an arro3 Table."""
    from arro3.io import read_parquet

    # arro3 panics on a file that is not Parquet. A Rust panic is a
    # BaseException, so check the magic bytes first, and catch it too.
    if not (data.startswith(_PARQUET_MAGIC) and data.endswith(_PARQUET_MAGIC)):
        raise FormatUnsuitable(
            f"load_url can't read {url} as GeoParquet: not a Parquet file"
        )
    try:
        return read_parquet(data).read_all()
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException as error:
        raise FormatUnsuitable(
            f"load_url can't read {url} as GeoParquet: {error}"
        ) from None


def _geoparquet_crs(column: Dict[str, Any]) -> CRS:
    """Return the CRS of a GeoParquet geometry column (PROJJSON, OGC:CRS84 if absent)."""
    crs = column.get("crs")
    if crs is None:
        return WGS84_CRS
    return CRS.from_user_input(json.dumps(crs) if isinstance(crs, dict) else crs)


def read_geoparquet(url: str, href: str, options: Dict) -> VectorCube:
    """Read a GeoParquet file (1.0 or 1.1) with WKB geometries."""
    table = _read_parquet_table(_download(url, href), url)
    metadata = table.schema.metadata or {}
    try:
        geo = json.loads(metadata[b"geo"])
        primary = geo["primary_column"]
        column = geo["columns"][primary]
    except (KeyError, TypeError, ValueError):
        raise FormatUnsuitable(
            f"load_url can't read {url} as GeoParquet: no valid `geo` metadata"
        ) from None
    if str(column.get("encoding", "")).upper() != "WKB":
        raise FormatUnsuitable(
            f"load_url reads GeoParquet geometries in the WKB encoding only, "
            f"got {column.get('encoding')!r}."
        )
    _check_count(table.num_rows)

    # Leave out the geometry columns and the bounding box columns.
    skip = set(geo["columns"])
    for spec in geo["columns"].values():
        skip.update(
            path[0] for path in (spec.get("covering") or {}).get("bbox", {}).values()
        )
    names = [name for name in table.column_names if name not in skip]
    columns = [table.column(name).to_pylist() for name in names]
    return VectorCube(
        geometries=_geometries(table.column(primary).to_pylist()),
        crs=_geoparquet_crs(column),
        properties=_rows(names, columns, table.num_rows),
    )
