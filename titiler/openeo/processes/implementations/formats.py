"""Output formats.

One registry for the formats that `save_result` writes. The `/file_formats`
endpoint and the HTTP responses read it too, so the advertised formats, the
accepted names and the media types can't drift apart.

This module is not star-imported into `implementations`: every public function
there must be an openEO process.
"""

import csv
import io
import json
import math
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Callable, Dict, Literal, Mapping, Optional, Tuple

import numpy
from rio_tiler.models import ImageData

from ...errors import FormatUnsuitable
from .data_model import RasterStack

DataKind = Literal["raster", "vector", "other"]
_DATA_KINDS: Tuple[DataKind, ...] = ("raster", "vector", "other")

Encoder = Callable[[Any, "OutputFormat", Dict], bytes]


@dataclass
class SaveResultData:
    """Container for result data with additional metadata."""

    data: bytes
    media_type: str
    metadata: Optional[Dict] = None

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


def data_kind(data: Any) -> DataKind:
    """Return the kind of data that a process graph result holds."""
    # RasterStack is a dict: test it before the FeatureCollection check.
    if isinstance(data, (RasterStack, ImageData, numpy.ndarray)):
        return "raster"
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
    # Get all ImageData objects from the RasterStack (in the same order as keys())
    # For RasterStack, this will execute all tasks but only when needed for saving
    image_data_list = list(data.values())

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
    for key, img in zip(data.keys(), image_data_list):
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
        "original_keys": [str(key) for key in data.keys()],
    }

    # Add any metadata from the original images
    for i, (key, img) in enumerate(data.items()):  # noqa: B007
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


_DATATYPE_DESCRIPTION = "The values data type."

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
        if not fmt.hidden and (kind is None or kind in fmt.encoders)
    )


def get_output_format(name: str) -> OutputFormat:
    """Return the output format for a name or an alias. Names are case-insensitive."""
    try:
        return _FORMATS_BY_NAME[name.lower()]
    except KeyError:
        raise FormatUnsuitable(
            f"Output format '{name}' is not supported. "
            f"Supported formats: {_format_names()}."
        ) from None


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
    return SaveResultData(
        data=encoder(data, fmt, dict(options or {})), media_type=fmt.media_type
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
            fmt.name: fmt.to_file_format() for fmt in OUTPUT_FORMATS if not fmt.hidden
        },
    }
