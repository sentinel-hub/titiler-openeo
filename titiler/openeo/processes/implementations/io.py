"""titiler.openeo.processes."""

import csv
import io
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Optional, Union

import numpy
from rio_tiler.constants import MAX_THREADS
from rio_tiler.io import COGReader
from rio_tiler.models import ImageData
from rio_tiler.tasks import create_tasks

from ...reader import _reader
from .data_model import RasterStack

__all__ = ["save_result", "SaveResultData", "load_url"]


def load_url(
    url: str, format: Optional[str] = None, options: Optional[Dict] = None
) -> RasterStack:
    """Load data from a URL.

    Args:
        url: The URL to read from (must be a valid HTTP/HTTPS URL)
        format: Input format (ignored for now, assumed to be COG)
        options: Additional reading options passed to rio-tiler

    Returns:
        RasterStack: A data cube containing the loaded data

    Raises:
        ValueError: If the URL is invalid
    """
    # Create a dummy STAC item for the COG
    item: Dict[str, Any] = {
        "type": "Feature",
        "id": "cog",
        "bbox": None,  # Will be set from the COG metadata
        "assets": {
            "data": {
                "href": url,
                "type": "image/tiff; application=geotiff; profile=cloud-optimized",
            }
        },
    }

    # Get metadata from COG to set bbox, dimensions, and CRS
    with COGReader(url) as cog:
        item["bbox"] = [float(x) for x in cog.bounds]
        cog_width = cog.dataset.width
        cog_height = cog.dataset.height
        cog_crs = cog.dataset.crs
        cog_bounds = tuple(float(x) for x in cog.bounds)

    # Create the tasks
    tasks = create_tasks(
        _reader,
        [item],
        MAX_THREADS,
        item["bbox"],
        assets=["data"],
    )

    # Return a RasterStack that will only execute the tasks when accessed
    return RasterStack(
        tasks=tasks,
        timestamp_fn=lambda _: datetime.now(),  # Use current time as timestamp
        allowed_exceptions=(),
        # New parameters for truly lazy behavior
        width=cog_width,
        height=cog_height,
        bounds=cog_bounds,
        dst_crs=cog_crs,
        band_names=["data"],
    )


@dataclass
class SaveResultData:
    """Container for result data with additional metadata."""

    data: bytes
    media_type: str
    metadata: Optional[Dict] = None

    def __bytes__(self) -> bytes:
        """Return the raw bytes data."""
        return self.data


def _render_geotiff_result(
    image_data: ImageData, options: Optional[Dict] = None
) -> SaveResultData:
    """Render an ImageData to a data GeoTIFF, preserving dtype, bands and nodata.

    Unlike PNG/JPEG, GTiff is a data output format: no uint8 cast, no
    colormap/RGB rendering and no alpha band. Masked pixels are encoded with a
    nodata value (NaN for floats, the array fill value otherwise) so the result
    round-trips as analysis-ready raster data. See issue #296.
    """
    opts = dict(options or {})
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

    rendered = image_data.render(img_format="GTIFF", **render_kwargs)
    return SaveResultData(data=rendered, media_type="image/tiff")


def _save_feature_collection(data: Dict, format: str) -> SaveResultData:
    """Serialize a GeoJSON FeatureCollection to JSON or CSV."""
    if format.lower() == "json":
        return SaveResultData(
            data=json.dumps(data).encode("utf-8"), media_type="application/json"
        )

    if format.lower() == "csv":
        # CSV output with date, feature_index, and value columns
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["date", "feature_index", "value"])
        for idx, feature in enumerate(data.get("features", [])):
            values_dict = feature.get("properties", {}).get("values", {})
            for date, value in values_dict.items():
                writer.writerow([date, idx, value])
        return SaveResultData(
            data=output.getvalue().encode("utf-8"), media_type="text/csv"
        )

    raise ValueError(
        "Only GeoJSON and CSV formats are supported for FeatureCollection data"
    )


def _save_single_result(
    data: Union[ImageData, numpy.ndarray, numpy.ma.MaskedArray, dict],
    format: str,
    options: Optional[Dict] = None,
) -> SaveResultData:
    """Save a single result (ImageData or numpy array)."""

    if isinstance(data, ImageData) and format.lower() == "metajson":
        # extract metadata from data
        metadata = data.metadata or {}
        # convert metadata to bytes
        bytes = json.dumps(metadata).encode("utf-8")
        return SaveResultData(data=bytes, media_type="application/json")

    if isinstance(data, dict) and data.get("type") == "FeatureCollection":
        return _save_feature_collection(data, format)

    if isinstance(data, (numpy.ma.MaskedArray, numpy.ndarray)):
        data = ImageData(data)

    # At this point, data should be an ImageData object
    # Add an explicit type check
    if not isinstance(data, ImageData):
        raise TypeError(f"Expected ImageData object, got {type(data).__name__}")

    image_data: ImageData = data
    options = options or {}

    # GTiff is a DATA format: preserve dtype, bands and nodata; never apply the
    # uint8/RGB image rendering used for PNG/JPEG. See issue #296.
    if format.lower() in ["tiff", "gtiff"]:
        return _render_geotiff_result(image_data, options)

    if format.lower() in ["jpeg", "jpg", "png"] and image_data.array.dtype != "uint8":
        # Convert to uint8 while preserving the mask if it's a masked array
        if isinstance(image_data.array, numpy.ma.MaskedArray):
            image_data.array = numpy.ma.array(
                image_data.array.data.astype("uint8"),
                mask=image_data.array.mask,
                fill_value=image_data.array.fill_value,
            )
        else:
            image_data.array = image_data.array.astype("uint8")

    # Get the appropriate media type based on the format
    format_lower = format.lower()
    if format_lower in ["jpeg", "jpg"]:
        media_type = "image/jpeg"
    elif format_lower == "png":
        media_type = "image/png"
    elif format_lower in ["tiff", "gtiff"]:
        media_type = "image/tiff"
    else:
        media_type = f"application/{format_lower}"

    # Render the data
    rendered_data = image_data.render(img_format=format_lower, **options)

    # Return the container with the rendered data and media type
    return SaveResultData(data=rendered_data, media_type=media_type)


def _handle_text_format(data: Any) -> SaveResultData:
    """Handle saving data in text/plain format.

    Args:
        data: The data to convert to text format

    Returns:
        SaveResultData: The data as text/plain bytes
    """
    if isinstance(data, dict):
        # If data is a dictionary, convert each item to string
        data = {k: str(v) for k, v in data.items()}
    else:
        # Otherwise, convert the entire data to string
        data = str(data)

    # Convert to bytes
    bytes_data = str(data).encode("utf-8")
    return SaveResultData(data=bytes_data, media_type="text/plain")


def _handle_json_format(data: Dict, format: str) -> SaveResultData:
    """Handle saving data in JSON/GeoJSON format.

    Args:
        data: Dictionary data to convert to JSON
        format: The target format (json or geojson)

    Returns:
        SaveResultData: The data as JSON bytes
    """
    # Convert to JSON and encode as bytes
    json_bytes = json.dumps(data).encode("utf-8")
    return SaveResultData(data=json_bytes, media_type="application/json")


def _handle_raster_geotiff(data: Dict[datetime, ImageData]) -> ImageData:
    """Handle combining multiple ImageData objects into a single multi-band GeoTIFF.

    Args:
        data: Dictionary mapping timestamps to ImageData objects

    Returns:
        ImageData: Combined multi-band image

    Raises:
        ValueError: If ImageData objects have incompatible properties
    """
    # Get all ImageData objects from the RasterStack
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

    # Stack all arrays into a single multi-band array
    # Each input array should be (1, height, width), and we want (bands, height, width)
    arrays = []
    for img in image_data_list:
        # Preserve the native dtype and mask: GTiff is a DATA format. Casting to
        # uint8 here collapses float index/reflectance values (e.g. NDVI in
        # [-1, 1] -> all zeros). See issue #296.
        arr = img.array
        # If array is 2D (height, width), add band dimension
        if arr.ndim == 2:
            arr = arr[numpy.newaxis, ...]
        # If array has multiple bands, take first band
        elif arr.shape[0] > 1:
            arr = arr[0:1, ...]
        arrays.append(arr)

    # Stack along band dimension
    stacked_array = numpy.ma.concatenate(arrays, axis=0)

    # Prepare metadata
    band_names_list = [str(key) for key in data.keys()]
    combined_metadata: Dict[str, Any] = {
        "band_names": band_names_list.copy(),
        "original_keys": band_names_list.copy(),
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


def save_result(
    data: Union[numpy.ndarray, numpy.ma.MaskedArray, RasterStack, dict],
    format: str,
    options: Optional[Dict] = None,
) -> Union[SaveResultData, Dict[str, SaveResultData]]:
    """Save Result.

    Args:
        data: numpy array or RasterStack to save
        format: Output format (e.g., 'png', 'jpeg', 'tiff')
        options: Additional rendering options

    Returns:
        For single images: ResultData containing the rendered image bytes and metadata
        For RasterStack: dictionary mapping keys to ResultData objects
    """
    # Handle text/plain format
    if format.lower() in ["txt", "plain"]:
        return _handle_text_format(data)

    # Handle JSON formats (but not RasterStack)
    if format.lower() in ["json", "geojson"]:
        # Check for GeoJSON FeatureCollection (plain dict, not RasterStack)
        if isinstance(data, dict) and not isinstance(data, RasterStack):
            plain_dict: Dict[str, Any] = data  # type: ignore[assignment]
            if plain_dict.get("type") == "FeatureCollection":
                return _save_single_result(data, format, options)
            return _handle_json_format(data, format)

    # Handle json for dictionaries structure (but not RasterStack)
    if (
        format.lower() in ["json", "geojson"]
        and isinstance(data, dict)
        and not isinstance(data, RasterStack)
    ):
        data = json.dumps(data).encode("utf-8")
        return SaveResultData(data=data, media_type="application/json")

    # Handle special cases for numpy arrays
    if isinstance(data, (numpy.ndarray, numpy.ma.MaskedArray)):
        # Create a RasterStack with a single ImageData
        data = RasterStack.from_images({datetime.now(): ImageData(data)})

    # If data is a RasterStack, handle appropriately
    if isinstance(data, RasterStack):
        # If there is only one item, save it as a single result
        if len(data) == 1:
            return _save_single_result(data.first, format, options)

        # For GeoTIFF format, combine all bands into a single multi-band image
        if format.lower() in ["tiff", "gtiff"]:
            combined_img = _handle_raster_geotiff(data)
            return _save_single_result(combined_img, format, options)

        # A multi-slice RasterStack cannot be written to a single-frame format
        # (PNG/JPEG and friends). This usually means an upstream operation kept a
        # temporal dimension that the caller expected to be collapsed — e.g.
        # merge_cubes received two cubes with non-matching time labels, so the
        # overlap_resolver was never applied and both slices were carried through.
        raise ValueError(
            f"Cannot save a RasterStack with {len(data)} temporal slices "
            f"(keys: {list(data.keys())}) to single-frame format "
            f"'{format}'. Use 'tiff'/'gtiff' to write all slices as a "
            "multi-band GeoTIFF, or collapse the temporal dimension first "
            "(e.g. reduce_dimension over 't', or align the time labels so "
            "merge_cubes can apply the overlap_resolver)."
        )

    # Otherwise, save as a single result
    return _save_single_result(data, format, options)
