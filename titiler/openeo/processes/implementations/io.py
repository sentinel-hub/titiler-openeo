"""titiler.openeo.processes."""

import csv
import io
import json
from dataclasses import dataclass
from typing import Any, Dict, Optional, Union

import numpy
from rio_tiler.constants import MAX_THREADS
from rio_tiler.io import COGReader
from rio_tiler.models import ImageData
from rio_tiler.tasks import create_tasks

from ...reader import _reader
from .data_model import LazyRasterStack, RasterStack

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

    # Get metadata from COG to set bbox
    with COGReader(url) as cog:
        item["bbox"] = [float(x) for x in cog.bounds]

    # Create the tasks
    tasks = create_tasks(
        _reader,
        [item],
        MAX_THREADS,
        item["bbox"],
        assets=["data"],
    )

    # Return a LazyRasterStack that will only execute the tasks when accessed
    return LazyRasterStack(
        tasks=tasks,
        date_name_fn=lambda _: "data",  # Single timestamp since it's a single COG
        allowed_exceptions=(),
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


def _save_single_result(
    data: Union[ImageData, numpy.ndarray, numpy.ma.MaskedArray, dict],
    format: str,
    options: Optional[Dict] = None,
) -> SaveResultData:
    """Save a single result (ImageData or numpy array)."""
    if isinstance(data, dict) and data.get("type") == "FeatureCollection":
        if format.lower() == "json":
            # convert json to bytes
            bytes = json.dumps(data).encode("utf-8")
            return SaveResultData(data=bytes, media_type="application/json")
        elif format.lower() == "csv":
            # Extract features from the FeatureCollection
            features = data.get("features", [])

            # Create CSV output with date, feature_index, and value columns
            output = io.StringIO()
            writer = csv.writer(output)

            # Check if this is brick quantity data
            sample_feature = features[0] if features else {}
            is_brick_data = "color" in sample_feature.get("properties", {})

            if is_brick_data:
                # Write header for brick data
                writer.writerow(["color", "quantity", "pantone", "hex", "transparent"])

                # Write data rows for brick quantities
                for feature in features:
                    properties = feature.get("properties", {})
                    values = properties.get("values", {})
                    
                    writer.writerow([
                        properties.get("color", ""),
                        values.get("quantity", 0),
                        properties.get("pantone", ""),
                        properties.get("hex", ""),
                        properties.get("transparent", False)
                    ])
            else:
                # Original behavior for other data types
                writer.writerow(["date", "feature_index", "value"])
                
                for idx, feature in enumerate(features):
                    properties = feature.get("properties", {})
                    values_dict = properties.get("values", {})

                    for date, value in values_dict.items():
                        writer.writerow([date, idx, value])

            # Convert to bytes
            csv_bytes = output.getvalue().encode("utf-8")
            return SaveResultData(data=csv_bytes, media_type="text/csv")

        raise ValueError(
            "Only GeoJSON and CSV formats are supported for FeatureCollection data"
        )

    if isinstance(data, (numpy.ma.MaskedArray, numpy.ndarray)):
        data = ImageData(data)

    # At this point, data should be an ImageData object
    # Add an explicit type check
    if not isinstance(data, ImageData):
        raise TypeError(f"Expected ImageData object, got {type(data).__name__}")

    image_data: ImageData = data
    options = options or {}

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
    # Handle txt/plain format
    if format.lower() in ["txt", "plain"]:
        # Convert data to string representation
        if isinstance(data, dict):
            # If data is a dictionary, convert each item to string
            data = {k: str(v) for k, v in data.items()}
        else:
            # Otherwise, convert the entire data to string
            data = str(data)

        # Convert to bytes
        bytes_data = str(data).encode("utf-8")
        return SaveResultData(data=bytes_data, media_type="text/plain")
    
    # Handle special cases for GeoJSON data directly
    if (
        format.lower() in ["json", "geojson"]
        and isinstance(data, dict)
        and data.get("type") == "FeatureCollection"
    ):
        return _save_single_result(data, format, options)

    # Handle special cases for numpy arrays
    if isinstance(data, (numpy.ndarray, numpy.ma.MaskedArray)):
        # Create a RasterStack with a single ImageData
        data = {"data": ImageData(data)}

    # If data is a RasterStack, handle appropriately
    if isinstance(data, dict):
        # If there is only one item, save it as a single result
        if len(data) == 1:
            return _save_single_result(list(data.values())[0], format, options)

        # For GeoTIFF format, combine all bands into a single multi-band image
        if format.lower() in ["tiff", "gtiff"]:
            # Get all ImageData objects from the RasterStack
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
                    raise ValueError(
                        "All images in RasterStack must have the same shape"
                    )
                if img.bounds != bounds:
                    raise ValueError(
                        "All images in RasterStack must have the same bounds"
                    )
                if img.crs != crs:
                    raise ValueError("All images in RasterStack must have the same CRS")

            # Stack all arrays into a single multi-band array
            # Each ImageData.array has shape (bands, height, width)
            # We need to extract the first band from each image (assuming single-band images)
            # and stack them into a new array with shape (num_images, height, width)
            arrays = [
                img.array[0] if img.array.ndim > 2 else img.array
                for img in image_data_list
            ]
            stacked_array = numpy.ma.stack(arrays)

            # Create a new ImageData object with the stacked array
            band_names_list = [str(key) for key in data.keys()]

            # Create metadata dictionary
            combined_metadata: Dict[str, Any] = {}
            combined_metadata["band_names"] = band_names_list.copy()
            combined_metadata["original_keys"] = band_names_list.copy()

            # Add any metadata from the original images
            for i, (key, img) in enumerate(data.items()):  # noqa: B007
                if img.metadata:
                    combined_metadata[f"band_{i}_metadata"] = img.metadata

            # Create the combined image
            combined_img = ImageData(
                stacked_array,
                bounds=bounds,
                crs=crs,
                metadata=combined_metadata,
                band_names=band_names_list,
            )

            # Save the combined image as a GeoTIFF
            return _save_single_result(combined_img, format, options)
    # Otherwise, save as a single result
    return _save_single_result(data, format, options)
