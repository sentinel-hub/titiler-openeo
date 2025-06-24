"""titiler.openeo.processes.implementations filter_bands methods."""

from typing import Dict, List, Optional, Union

from .data_model import ImageData, RasterStack

__all__ = ["filter_bands"]


def _filter_bands_image(
    data: ImageData,
    bands: Optional[List[str]] = None,
    wavelengths: Optional[List[List[float]]] = None,
) -> ImageData:
    """Filter bands from a single ImageData.

    Args:
        data: ImageData to process
        bands: List of band names to keep
        wavelengths: List of wavelength ranges [[min1, max1], [min2, max2], ...]

    Returns:
        ImageData with filtered bands

    Raises:
        ValueError: If no bands match the filter criteria
        ValueError: If no filter criteria are provided
    """
    if not bands and not wavelengths:
        raise ValueError(
            "BandFilterParameterMissing: At least one filter parameter must be specified"
        )

    if not data.band_names:
        raise ValueError("DimensionMissing: Band dimension is missing")

    selected_indices = []

    if bands:
        # Match both unique band names and common names
        # Note: In a real implementation you would need to also check common_names
        # from the band metadata, here we just use band_names as an example
        for band in bands:
            for i, name in enumerate(data.band_names):
                if name == band:
                    selected_indices.append(i)

    if wavelengths:
        # Check each band's wavelength against the ranges
        # Note: In a real implementation you would need to get actual wavelength
        # information from band metadata
        pass  # Wavelength filtering would go here

    if not selected_indices:
        raise ValueError("No bands match the filter criteria")

    # Get unique indices while preserving order
    selected_indices = list(dict.fromkeys(selected_indices))

    return ImageData(
        data.array[selected_indices],
        assets=data.assets,
        crs=data.crs,
        bounds=data.bounds,
        band_names=[data.band_names[i] for i in selected_indices],
        metadata=data.metadata,
        dataset_statistics=data.dataset_statistics[selected_indices]
        if data.dataset_statistics
        else None,
        cutline_mask=data.cutline_mask,
    )


def filter_bands(
    data: RasterStack,
    bands: Optional[List[str]] = None,
    wavelengths: Optional[List[List[float]]] = None,
) -> RasterStack:
    """Filter bands from a RasterStack.

    Args:
        data: RasterStack to process
        bands: List of band names to keep
        wavelengths: List of wavelength ranges [[min1, max1], [min2, max2], ...]

    Returns:
        RasterStack with filtered bands
    """
    # Apply to each item in the RasterStack
    result: Dict[str, ImageData] = {}
    for key, img_data in data.items():
        result[key] = _filter_bands_image(img_data, bands, wavelengths)
    return result
