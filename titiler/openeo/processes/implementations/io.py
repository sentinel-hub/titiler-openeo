"""titiler.openeo.processes."""

from dataclasses import dataclass
from typing import Any, Dict, Optional, Union

import numpy
from rio_tiler.models import ImageData

from .data_model import RasterStack

__all__ = ["save_result", "SaveResultData"]


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
    if isinstance(data, (numpy.ma.MaskedArray, numpy.ndarray)):
        data = ImageData(data)

    options = options or {}

    if format.lower() in ["jpeg", "jpg", "png"] and data.array.dtype != "uint8":
        # Convert to uint8 while preserving the mask if it's a masked array
        if isinstance(data.array, numpy.ma.MaskedArray):
            data.array = numpy.ma.array(
                data.array.data.astype("uint8"),
                mask=data.array.mask,
                fill_value=data.array.fill_value,
            )
        else:
            data.array = data.array.astype("uint8")

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
    rendered_data = data.render(img_format=format_lower, **options)

    # Return the container with the rendered data and media type
    return SaveResultData(data=rendered_data, media_type=media_type)


def save_result(
    data: Union[ImageData, numpy.ndarray, numpy.ma.MaskedArray, RasterStack],
    format: str,
    options: Optional[Dict] = None,
) -> Union[SaveResultData, Dict[str, SaveResultData]]:
    """Save Result.

    Args:
        data: ImageData, numpy array, or RasterStack to save
        format: Output format (e.g., 'png', 'jpeg', 'tiff')
        options: Additional rendering options

    Returns:
        For single images: ResultData containing the rendered image bytes and metadata
        For RasterStack: dictionary mapping keys to ResultData objects
    """
    # If data is a RasterStack (dictionary), save each item
    if isinstance(data, RasterStack):
        if data.__len__() == 1:
            # If there is only one item, save it as a single result
            return _save_single_result(list(data.values())[0], format, options)

        # For GeoTIFF format, combine all bands into a single multi-band image
        if format.lower() in ["tiff", "gtiff"]:
            # Get all ImageData objects from the RasterStack
            image_data_list = list(data.values())

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

        # For other formats, save each band separately
        results = {}
        for key, img_data in data.items():
            results[key] = _save_single_result(img_data, format, options)
        return results

    # Otherwise, save as a single result
    return _save_single_result(data, format, options)
