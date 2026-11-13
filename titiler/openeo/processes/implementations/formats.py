"""Output formats.

One registry for the formats that `save_result` writes. The `/file_formats`
endpoint and the HTTP responses read it too, so the advertised formats, the
accepted names and the media types can't drift apart.

This module is not star-imported into `implementations`: every public function
there must be an openEO process.
"""

import csv
import importlib.util
import io
import json
import math
import os
import tempfile
import zipfile
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from typing import Any, Callable, Dict, List, Literal, Mapping, Optional, Tuple

import numpy
from rio_tiler.models import ImageData

from ...errors import FormatUnsuitable, ProcessParameterInvalid
from .data_model import RasterStack
from .vector_model import VectorCube, VectorRecords

DataKind = Literal["raster", "vector", "other"]
_DATA_KINDS: Tuple[DataKind, ...] = ("raster", "vector", "other")

Encoder = Callable[[Any, "OutputFormat", Dict], bytes]


@dataclass
class SaveResultData:
    """Container for result data with additional metadata."""

    data: bytes
    media_type: str
    metadata: Optional[Dict] = None
    # The file name for the `Content-Disposition` header, if any.
    filename: Optional[str] = None

    def __bytes__(self) -> bytes:
        """Return the raw bytes data."""
        return self.data


@dataclass(frozen=True)
class OutputFormat:
    """An output file format and its encoders, by kind of data."""

    name: str
    title: str
    description: str
    media_type: str
    encoders: Mapping[DataKind, Encoder]
    aliases: Tuple[str, ...] = ()
    parameters: Mapping[str, Any] = field(default_factory=dict)
    # Accepted by `save_result`, but not listed in `/file_formats`.
    hidden: bool = False
    # The file extension. A format with an extension is sent as a file
    # download (`Content-Disposition`).
    extension: Optional[str] = None
    # The modules that the encoders need (an optional extra).
    requires: Tuple[str, ...] = ()
    # The vector encoder takes a VectorCube, not a GeoJSON FeatureCollection.
    takes_cube: bool = False

    @property
    def available(self) -> bool:
        """Return True if the modules of this format are installed."""
        return all(_module_available(name) for name in self.requires)

    @property
    def gis_data_types(self) -> list:
        """Kinds of data that this format can store, in the openEO vocabulary."""
        return [kind for kind in _DATA_KINDS if kind in self.encoders]

    def to_file_format(self) -> Dict[str, Any]:
        """Return the openEO `FileFormat` document of this format."""
        return {
            "title": self.title,
            "description": self.description,
            "gis_data_types": self.gis_data_types,
            "parameters": dict(self.parameters),
        }


@lru_cache(maxsize=None)
def _module_available(name: str) -> bool:
    """Return True if the module can be imported."""
    try:
        return importlib.util.find_spec(name) is not None
    except ModuleNotFoundError:  # The parent package of a submodule is missing
        return False


def data_kind(data: Any) -> DataKind:
    """Return the kind of data that a process graph result holds."""
    # RasterStack is a dict: test it before the FeatureCollection check.
    if isinstance(data, (RasterStack, ImageData, numpy.ndarray)):
        return "raster"
    if isinstance(data, VectorCube):
        return "vector"
    if isinstance(data, dict) and data.get("type") == "FeatureCollection":
        return "vector"
    return "other"


def _handle_raster_geotiff(data: Dict[datetime, ImageData]) -> ImageData:
    """Handle combining multiple ImageData objects into a single multi-band GeoTIFF.

    Args:
        data: Dictionary mapping timestamps to ImageData objects

    Returns:
        ImageData: Combined multi-band image

    Raises:
        ValueError: If ImageData objects have incompatible properties
    """
    # Get all (key, ImageData) pairs from the RasterStack, in temporal order.
    # For RasterStack, this will execute all tasks but only when needed for saving.
    # Pairs, not keys() zipped with values(): a slice whose task is skipped has
    # no image, and zipping would label the next date's pixels with its date.
    slices = list(data.items())
    image_data_list = [img for _, img in slices]

    # Check if this is a RasterStack with ImageData objects
    if not all(isinstance(img, ImageData) for img in image_data_list):
        raise ValueError(
            "All items in RasterStack must be ImageData objects to save as GeoTIFF"
        )

    # Check if all ImageData objects have the same shape, bounds, and CRS
    first_img = image_data_list[0]
    shape = first_img.array.shape[1:]  # Height, Width
    bounds = first_img.bounds
    crs = first_img.crs

    for img in image_data_list[1:]:
        if img.array.shape[1:] != shape:
            raise ValueError("All images in RasterStack must have the same shape")
        if img.bounds != bounds:
            raise ValueError("All images in RasterStack must have the same bounds")
        if img.crs != crs:
            raise ValueError("All images in RasterStack must have the same CRS")

    # Stack every band of every slice into one (bands, height, width) array:
    # the output has the bands of the first date, then the bands of the next one.
    arrays = []
    band_names_list = []
    for key, img in slices:
        # Preserve the native dtype and mask: GTiff is a DATA format. Casting to
        # uint8 here collapses float index/reflectance values (e.g. NDVI in
        # [-1, 1] -> all zeros). See issue #296.
        arr = img.array
        # If array is 2D (height, width), add band dimension
        if arr.ndim == 2:
            arr = arr[numpy.newaxis, ...]
        arrays.append(arr)

        # A single-band slice is named by its date; the bands of a multi-band
        # slice by date and band.
        if arr.shape[0] == 1:
            band_names_list.append(str(key))
        else:
            names = img.band_descriptions
            if len(names) != arr.shape[0]:
                names = [f"b{idx + 1}" for idx in range(arr.shape[0])]
            band_names_list.extend(f"{key}_{name}" for name in names)

    # Stack along band dimension
    stacked_array = numpy.ma.concatenate(arrays, axis=0)

    # Prepare metadata
    combined_metadata: Dict[str, Any] = {
        "band_names": band_names_list.copy(),
        "original_keys": [str(key) for key, _ in slices],
    }

    # Add any metadata from the original images
    for i, (key, img) in enumerate(slices):  # noqa: B007
        if img.metadata:
            combined_metadata[f"band_{i}_metadata"] = img.metadata

    return ImageData(
        stacked_array,
        bounds=bounds,
        crs=crs,
        metadata=combined_metadata,
        band_descriptions=band_names_list,
    )


def _as_image(data: Any, fmt: OutputFormat, multi_slice: bool = False) -> ImageData:
    """Return the one image that a raster format writes.

    A RasterStack with one slice gives that slice. With more slices, the slices
    become the bands of one image if `multi_slice` is set, and an error if not.
    """
    if isinstance(data, ImageData):
        return data
    if isinstance(data, numpy.ndarray):
        return ImageData(data)
    if len(data) == 1:
        return data.first
    if multi_slice:
        return _handle_raster_geotiff(data)

    # A multi-slice RasterStack cannot be written to a single-frame format
    # (PNG/JPEG and friends). This usually means an upstream operation kept a
    # temporal dimension that the caller expected to be collapsed — e.g.
    # merge_cubes received two cubes with non-matching time labels, so the
    # overlap_resolver was never applied and both slices were carried through.
    raise ValueError(
        f"Cannot save a RasterStack with {len(data)} temporal slices "
        f"(keys: {list(data.keys())}) to single-frame format "
        f"'{fmt.name}'. Use 'tiff'/'gtiff' to write all slices as a "
        "multi-band GeoTIFF, or collapse the temporal dimension first "
        "(e.g. reduce_dimension over 't', or align the time labels so "
        "merge_cubes can apply the overlap_resolver)."
    )


def _encode_geotiff(data: Any, fmt: OutputFormat, options: Dict) -> bytes:
    """Render a raster to a data GeoTIFF, preserving dtype, bands and nodata.

    Unlike PNG/JPEG, GTiff is a data output format: no uint8 cast, no
    colormap/RGB rendering and no alpha band. Masked pixels are encoded with a
    nodata value (NaN for floats, the array fill value otherwise) so the result
    round-trips as analysis-ready raster data. See issue #296.
    """
    image_data = _as_image(data, fmt, multi_slice=True)
    opts = dict(options)
    arr = image_data.array
    nodata = opts.pop("nodata", None)

    # Only encode nodata when there are actually masked pixels. For floats NaN is
    # the natural nodata; for integers use the array fill value, clamped to the
    # dtype range (the default masked fill of 999999 overflows e.g. uint8).
    has_mask = isinstance(arr, numpy.ma.MaskedArray) and bool(numpy.ma.is_masked(arr))
    if nodata is None and has_mask:
        if numpy.issubdtype(arr.dtype, numpy.floating):
            nodata = float(numpy.nan)
        else:
            info = numpy.iinfo(arr.dtype)
            fill = int(arr.fill_value)
            nodata = fill if info.min <= fill <= info.max else int(info.max)

    if has_mask and nodata is not None:
        # Bake the mask into the data band as nodata (rendered with add_mask=False
        # below), so masked pixels are written as nodata rather than raw values.
        image_data = ImageData(
            numpy.ma.array(arr.filled(nodata), mask=False),
            crs=image_data.crs,
            bounds=image_data.bounds,
            band_descriptions=image_data.band_descriptions,
            metadata=image_data.metadata,
        )

    render_kwargs: Dict[str, Any] = {"add_mask": False}
    if nodata is not None:
        render_kwargs["nodata"] = nodata
    render_kwargs.update(opts)

    return image_data.render(img_format="GTIFF", **render_kwargs)


def _encode_image(data: Any, fmt: OutputFormat, options: Dict) -> bytes:
    """Render a raster to an 8-bit image format (PNG, JPEG)."""
    image_data = _as_image(data, fmt)
    if image_data.array.dtype != "uint8":
        # Convert to uint8 while preserving the mask if it's a masked array
        if isinstance(image_data.array, numpy.ma.MaskedArray):
            image_data.array = numpy.ma.array(
                image_data.array.data.astype("uint8"),
                mask=image_data.array.mask,
                fill_value=image_data.array.fill_value,
            )
        else:
            image_data.array = image_data.array.astype("uint8")

    return image_data.render(img_format=fmt.name.lower(), **options)


def _to_json_safe(obj: Any) -> Any:
    """Return a copy of the data that holds only JSON types.

    Vector cube values are keyed by `datetime` and are numpy scalars, masked
    values or NaN when a geometry has no valid pixel. Dates become ISO 8601
    strings, and masked or non-finite numbers become `null`.
    """
    if isinstance(obj, dict):
        return {
            key if isinstance(key, str) else str(_to_json_safe(key)): _to_json_safe(
                value
            )
            for key, value in obj.items()
        }
    if isinstance(obj, (list, tuple)):
        return [_to_json_safe(value) for value in obj]
    # Test the masked constant before ndarray: it is an ndarray subclass.
    if obj is numpy.ma.masked:
        return None
    if isinstance(obj, numpy.ndarray):
        return _to_json_safe(obj.tolist())
    if isinstance(obj, numpy.generic):
        return _to_json_safe(obj.item())
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, float) and not math.isfinite(obj):
        return None
    return obj


def _json_bytes(data: Any) -> bytes:
    return json.dumps(_to_json_safe(data), allow_nan=False).encode("utf-8")


def _encode_image_metadata(data: Any, fmt: OutputFormat, options: Dict) -> bytes:
    """Write the metadata of a raster image as JSON."""
    return _json_bytes(_as_image(data, fmt).metadata or {})


def _encode_json(data: Any, fmt: OutputFormat, options: Dict) -> bytes:
    """Write the data as JSON."""
    return _json_bytes(data)


def _encode_feature_csv(data: Dict, fmt: OutputFormat, options: Dict) -> bytes:
    """Write a vector cube as CSV, with date and feature_index columns.

    A scalar value goes in the `value` column. A dict value (one entry per
    band of a multi-band cube) gives one column per key.
    """
    rows = [
        (label, idx, value)
        for idx, feature in enumerate(data.get("features", []))
        for label, value in feature.get("properties", {}).get("values", {}).items()
    ]

    columns: Dict[str, None] = {}
    for _, _, value in rows:
        for key in value if isinstance(value, dict) else ["value"]:
            columns[key] = None

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["date", "feature_index", *(columns or ["value"])])
    for label, idx, value in rows:
        cells = value if isinstance(value, dict) else {"value": value}
        # Same ISO 8601 dates as the JSON output; a masked value is empty.
        writer.writerow(
            [_to_json_safe(label), idx]
            + [_to_json_safe(cells.get(column)) for column in columns]
        )
    return output.getvalue().encode("utf-8")


def _encode_text(data: Any, fmt: OutputFormat, options: Dict) -> bytes:
    """Write the string representation of the data."""
    if isinstance(data, dict):
        # If data is a dictionary, convert each item to string
        data = {k: str(v) for k, v in data.items()}
    return str(data).encode("utf-8")


ColumnKind = Literal["bool", "int", "float", "datetime", "str"]

_GEOMETRY_COLUMN = "geometry"
_DEFAULT_LAYER = "result"
_EPOCH = datetime(1970, 1, 1)
_MICROSECOND = timedelta(microseconds=1)


def _as_vector_cube(data: Any) -> VectorCube:
    """Return a VectorCube for vector data (a GeoJSON FeatureCollection is converted)."""
    return data if isinstance(data, VectorCube) else VectorCube.from_geojson(data)


def _column_kind(values: List[Any]) -> ColumnKind:
    """Return the type of a table column, from its values that are not null."""
    present = [value for value in values if value is not None]
    if present and all(isinstance(v, bool) for v in present):
        return "bool"
    if present and all(isinstance(v, int) and not isinstance(v, bool) for v in present):
        return "int"
    if present and all(
        isinstance(v, (int, float)) and not isinstance(v, bool) for v in present
    ):
        return "float"
    if present and all(isinstance(v, (datetime, date)) for v in present):
        return "datetime"
    return "str"


def _as_datetime(value: Any) -> datetime:
    """Return a naive UTC datetime. The dates of a cube are naive UTC."""
    if not isinstance(value, datetime):
        value = datetime(value.year, value.month, value.day)
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def _as_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def _shapes(records: VectorRecords) -> numpy.ndarray:
    """Return the shapely geometries of the rows (None for a null geometry)."""
    from shapely.geometry import shape

    return numpy.array(
        [shape(g) if g else None for g in records.geometries], dtype=object
    )


def _ogr_geometry_type(geometries: Any) -> Tuple[str, bool]:
    """Return the OGR geometry type of a layer, and whether to promote to multi."""
    types = {g.geom_type for g in geometries if g is not None}
    if len(types) == 1:
        return types.pop(), False
    bases = {t.removeprefix("Multi") for t in types}
    if len(bases) == 1:
        return "Multi" + bases.pop(), True
    return "Unknown", False


def _ogr_fields(
    records: VectorRecords, dates_as_text: bool = False
) -> Tuple[List[str], List[Any], List[Optional[Any]]]:
    """Return the names, the numpy arrays and the null masks of the OGR fields.

    With `dates_as_text`, dates are ISO 8601 text (a Shapefile has no datetime
    field type).
    """
    names, arrays, masks = [], [], []
    for name, values in records.columns.items():
        kind = _column_kind(values)
        if kind == "datetime" and dates_as_text:
            kind = "str"
        mask = numpy.array([v is None for v in values], dtype=bool)
        if kind == "datetime":
            array = numpy.array(
                [
                    numpy.datetime64(_as_datetime(v), "ms") if v is not None else None
                    for v in values
                ],
                dtype="datetime64[ms]",
            )
        elif kind == "str":
            array = numpy.array([_as_text(v) for v in values], dtype=object)
        else:
            dtype = {"bool": bool, "int": "int64", "float": "float64"}[kind]
            array = numpy.array([0 if v is None else v for v in values], dtype=dtype)
        names.append(name)
        arrays.append(array)
        masks.append(mask if mask.any() else None)
    return names, arrays, masks


def _ogr_write(data: Any, target: Any, driver: str, layer: str, **kwargs: Any) -> None:
    """Write vector data with pyogrio (OGR), in the CRS of the cube."""
    import shapely
    from pyogrio.raw import write

    records = _as_vector_cube(data).to_records()
    geometries = _shapes(records)
    geometry_type, promote = _ogr_geometry_type(geometries)
    if driver == "ESRI Shapefile" and geometry_type == "Unknown":
        raise FormatUnsuitable(
            "Output format 'ESRI Shapefile' stores one geometry type only. "
            "Use GPKG, FlatGeobuf or GeoParquet for mixed geometry types."
        )
    names, arrays, masks = _ogr_fields(
        records, dates_as_text=driver == "ESRI Shapefile"
    )
    write(
        target,
        shapely.to_wkb(geometries),
        arrays,
        names,
        field_mask=masks,
        layer=layer,
        driver=driver,
        geometry_type=geometry_type,
        promote_to_multi=promote,
        crs=records.crs.to_wkt(),
        **kwargs,
    )


def _option(options: Dict, name: str, default: str) -> str:
    """Return a string format option."""
    value = options.get(name, default)
    if not isinstance(value, str) or not value:
        raise ProcessParameterInvalid(
            f"Format option '{name}' must be a non-empty string, got {value!r}"
        )
    return value


def _encode_gpkg(data: Any, fmt: "OutputFormat", options: Dict) -> bytes:
    """Write vector data as a GeoPackage."""
    target = io.BytesIO()
    _ogr_write(data, target, "GPKG", _option(options, "layer_name", _DEFAULT_LAYER))
    return target.getvalue()


def _encode_flatgeobuf(data: Any, fmt: "OutputFormat", options: Dict) -> bytes:
    """Write vector data as FlatGeobuf."""
    target = io.BytesIO()
    _ogr_write(data, target, "FlatGeobuf", _DEFAULT_LAYER)
    return target.getvalue()


def _encode_shapefile(data: Any, fmt: "OutputFormat", options: Dict) -> bytes:
    """Write vector data as a zipped ESRI Shapefile (.shp, .shx, .dbf, .prj, .cpg)."""
    encoding = _option(options, "encoding", "UTF-8")
    with tempfile.TemporaryDirectory() as directory:
        _ogr_write(
            data,
            os.path.join(directory, f"{_DEFAULT_LAYER}.shp"),
            "ESRI Shapefile",
            _DEFAULT_LAYER,
            encoding=encoding,
        )
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zipped:
            for name in sorted(os.listdir(directory)):
                zipped.write(os.path.join(directory, name), name)
    return archive.getvalue()


# arro3 needs a level for these codecs; this is the level of pyarrow.
_PARQUET_COMPRESSION = {
    "uncompressed": "uncompressed",
    "snappy": "snappy",
    "gzip": "gzip(6)",
    "brotli": "brotli(1)",
    "zstd": "zstd(1)",
    "lz4": "lz4_raw",
}


def _arro3_array(values: List[Any]) -> Any:
    """Return an arro3 Array for a table column (None is null)."""
    from arro3.core import Array, DataType

    kind = _column_kind(values)
    if kind == "datetime":
        micros = [
            None if v is None else (_as_datetime(v) - _EPOCH) // _MICROSECOND
            for v in values
        ]
        return Array(micros, DataType.int64()).cast(DataType.timestamp("us"))
    if kind == "str":
        return Array([_as_text(v) for v in values], DataType.string())
    data_type = {
        "bool": DataType.bool(),
        "int": DataType.int64(),
        "float": DataType.float64(),
    }[kind]
    return Array(values, data_type)


def _encode_geoparquet(data: Any, fmt: "OutputFormat", options: Dict) -> bytes:
    """Write vector data as GeoParquet 1.1, with WKB geometries."""
    import shapely
    from arro3.core import Array, DataType, Table
    from arro3.io import write_parquet
    from pyproj import CRS as ProjCRS

    compression = _option(options, "compression", "zstd").lower()
    if compression not in _PARQUET_COMPRESSION:
        raise ProcessParameterInvalid(
            f"Format option 'compression' must be one of "
            f"{', '.join(_PARQUET_COMPRESSION)}, got {compression!r}"
        )

    records = _as_vector_cube(data).to_records(reserved=(_GEOMETRY_COLUMN,))
    geometries = _shapes(records)
    columns = {name: _arro3_array(values) for name, values in records.columns.items()}
    columns[_GEOMETRY_COLUMN] = Array(
        list(shapely.to_wkb(geometries)), DataType.binary()
    )
    present = [g for g in geometries if g is not None]
    geo = {
        "version": "1.1.0",
        "primary_column": _GEOMETRY_COLUMN,
        "columns": {
            _GEOMETRY_COLUMN: {
                "encoding": "WKB",
                "geometry_types": sorted({g.geom_type for g in present}),
                "crs": ProjCRS.from_user_input(records.crs.to_wkt()).to_json_dict(),
                **(
                    {"bbox": [float(b) for b in shapely.total_bounds(present)]}
                    if present
                    else {}
                ),
            }
        },
    }
    target = io.BytesIO()
    # The `geo` key must be in the file metadata. With the Arrow schema also in
    # the file, pyarrow (and so geopandas) would not see the key.
    write_parquet(
        Table.from_pydict(columns),
        target,
        compression=_PARQUET_COMPRESSION[compression],
        key_value_metadata={"geo": json.dumps(geo)},
        skip_arrow_metadata=True,
    )
    return target.getvalue()


_DATATYPE_DESCRIPTION = "The values data type."

# The modules of the optional `vector` extra (ADR 0014 §2.6).
_VECTOR_EXTRA = ("pyogrio", "shapely", "arro3.io")

OUTPUT_FORMATS: Tuple[OutputFormat, ...] = (
    OutputFormat(
        name="JPEG",
        title="Joint Photographic Experts Group",
        description="JPEG is an image format that uses lossy compression and is one of the most widely used image formats. Compared to other EO raster formats, it is less flexible and standardized regarding number of bands, embedding geospatial metadata, etc.",
        media_type="image/jpeg",
        encoders={"raster": _encode_image},
        aliases=("jpg",),
        parameters={
            "datatype": {
                "type": "string",
                "description": _DATATYPE_DESCRIPTION,
                "enum": ["byte"],
                "default": "byte",
            }
        },
    ),
    OutputFormat(
        name="PNG",
        title="Portable Network Graphics (PNG)",
        description="PNG is a popular raster format used for graphics on the web. Compared to other EO raster formats, it is less flexible and standardized regarding number of bands, embedding geospatial metadata, etc.",
        media_type="image/png",
        encoders={"raster": _encode_image},
        parameters={
            "datatype": {
                "type": "string",
                "description": _DATATYPE_DESCRIPTION,
                "enum": ["byte", "uint16"],
                "default": "byte",
            }
        },
    ),
    OutputFormat(
        name="GTiff",
        title="GeoTIFF",
        description="GeoTIFF is a public domain metadata standard which allows georeferencing information to be embedded within a TIFF file. The potential additional information includes map projection, coordinate systems, ellipsoids, datums, and everything else necessary to establish the exact spatial reference for the file.",
        media_type="image/tiff",
        encoders={"raster": _encode_geotiff},
        aliases=("tiff",),
        parameters={
            "datatype": {
                "type": "string",
                "description": _DATATYPE_DESCRIPTION,
                "enum": [
                    "byte",
                    "uint16",
                    "int16",
                    "uint32",
                    "int32",
                    "float32",
                    "float64",
                ],
                "default": "byte",
            }
        },
    ),
    OutputFormat(
        name="JSON",
        title="JavaScript Object Notation (JSON)",
        description="JSON document. A vector cube is written as a GeoJSON FeatureCollection.",
        media_type="application/json",
        encoders={"vector": _encode_json, "other": _encode_json},
    ),
    OutputFormat(
        name="GeoJSON",
        title="GeoJSON",
        description="A vector cube as a GeoJSON FeatureCollection. The `values` property of each feature maps the ISO 8601 dates to the aggregated values. For a cube with more than one band, each value maps the band names to the values of the bands.",
        media_type="application/geo+json",
        encoders={"vector": _encode_json},
    ),
    OutputFormat(
        name="CSV",
        title="Comma-Separated Values (CSV)",
        description="A vector cube as a table with the columns `date`, `feature_index` and `value`. For a cube with more than one band, there is one column for each band, in place of `value`.",
        media_type="text/csv",
        encoders={"vector": _encode_feature_csv},
    ),
    OutputFormat(
        name="GeoParquet",
        title="GeoParquet",
        description="A vector cube as a GeoParquet 1.1 file, with WKB geometries in the coordinate reference system of the cube. There is one row for each geometry and date, with a column `t` for the date and one column for each band (or `value`).",
        media_type="application/vnd.apache.parquet",
        encoders={"vector": _encode_geoparquet},
        extension="parquet",
        requires=_VECTOR_EXTRA,
        takes_cube=True,
        parameters={
            "compression": {
                "type": "string",
                "description": "The compression codec.",
                "enum": list(_PARQUET_COMPRESSION),
                "default": "zstd",
            }
        },
    ),
    OutputFormat(
        name="GPKG",
        title="GeoPackage",
        description="A vector cube as an OGC GeoPackage, in the coordinate reference system of the cube. There is one row for each geometry and date, with a column `t` for the date and one column for each band (or `value`).",
        media_type="application/geopackage+sqlite3",
        encoders={"vector": _encode_gpkg},
        aliases=("geopackage",),
        extension="gpkg",
        requires=_VECTOR_EXTRA,
        takes_cube=True,
        parameters={
            "layer_name": {
                "type": "string",
                "description": "The name of the layer.",
                "default": _DEFAULT_LAYER,
            }
        },
    ),
    OutputFormat(
        name="FlatGeobuf",
        title="FlatGeobuf",
        description="A vector cube as FlatGeobuf, in the coordinate reference system of the cube. There is one row for each geometry and date, with a column `t` for the date and one column for each band (or `value`).",
        media_type="application/vnd.flatgeobuf",
        encoders={"vector": _encode_flatgeobuf},
        aliases=("fgb",),
        extension="fgb",
        requires=_VECTOR_EXTRA,
        takes_cube=True,
    ),
    OutputFormat(
        name="ESRI Shapefile",
        title="ESRI Shapefile (zip)",
        description="A vector cube as an ESRI Shapefile in a zip archive, in the coordinate reference system of the cube. A Shapefile stores one geometry type only, its field names have a maximum of 10 characters, and dates are stored as text.",
        media_type="application/zip",
        encoders={"vector": _encode_shapefile},
        aliases=("shapefile", "shp"),
        extension="shp.zip",
        requires=_VECTOR_EXTRA,
        takes_cube=True,
        parameters={
            "encoding": {
                "type": "string",
                "description": "The character encoding of the attributes.",
                "default": "UTF-8",
            }
        },
    ),
    OutputFormat(
        name="metajson",
        title="Image metadata (JSON)",
        description="The metadata of a raster image as JSON.",
        media_type="application/json",
        encoders={"raster": _encode_image_metadata},
        hidden=True,
    ),
    OutputFormat(
        name="txt",
        title="Plain text",
        description="The string representation of the data.",
        media_type="text/plain",
        encoders={kind: _encode_text for kind in _DATA_KINDS},
        aliases=("plain",),
        hidden=True,
    ),
)

_FORMATS_BY_NAME: Dict[str, OutputFormat] = {
    key.lower(): fmt for fmt in OUTPUT_FORMATS for key in (fmt.name, *fmt.aliases)
}


def _format_names(kind: Optional[DataKind] = None) -> str:
    return ", ".join(
        fmt.name
        for fmt in OUTPUT_FORMATS
        if not fmt.hidden and fmt.available and (kind is None or kind in fmt.encoders)
    )


def get_output_format(name: str) -> OutputFormat:
    """Return the output format for a name or an alias. Names are case-insensitive."""
    try:
        fmt = _FORMATS_BY_NAME[name.lower()]
    except KeyError:
        raise FormatUnsuitable(
            f"Output format '{name}' is not supported. "
            f"Supported formats: {_format_names()}."
        ) from None
    if not fmt.available:
        raise FormatUnsuitable(
            f"Output format '{fmt.name}' is not installed on this back-end "
            f"(Python package extra `titiler-openeo[vector]`). "
            f"Supported formats: {_format_names()}."
        )
    return fmt


def encode(data: Any, format: str, options: Optional[Dict] = None) -> SaveResultData:
    """Encode the data in an output format."""
    fmt = get_output_format(format)
    kind = data_kind(data)
    encoder = fmt.encoders.get(kind)
    if encoder is None:
        raise FormatUnsuitable(
            f"Output format '{fmt.name}' can't store {kind} data. "
            f"Formats for {kind} data: {_format_names(kind)}."
        )
    if isinstance(data, VectorCube) and not fmt.takes_cube:
        # These encoders write a GeoJSON FeatureCollection (EPSG:4326).
        data = data.to_feature_collection()
    return SaveResultData(
        data=encoder(data, fmt, dict(options or {})),
        media_type=fmt.media_type,
        filename=f"result.{fmt.extension}" if fmt.extension else None,
    )


def encode_result(result: Any, raster_format: str = "GTiff") -> SaveResultData:
    """Return the encoded result of a process graph.

    A graph without `save_result` returns the data itself. Encode it in a
    default format: `raster_format` for a raster, JSON for other data.
    """
    if isinstance(result, SaveResultData):
        return result
    if isinstance(result, str):
        return encode(result, "txt")
    if data_kind(result) == "raster":
        return encode(result, raster_format)
    return encode(result, "JSON")


def file_formats_document() -> Dict[str, Any]:
    """Return the body of the openEO `/file_formats` endpoint."""
    return {
        "input": {},
        "output": {
            fmt.name: fmt.to_file_format()
            for fmt in OUTPUT_FORMATS
            if not fmt.hidden and fmt.available
        },
    }
