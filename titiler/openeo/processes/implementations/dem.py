"""titiler.openeo.processes dem."""

import numpy
from rasterio import windows

from .data_model import ImageData, RasterStack

__all__ = ["hillshade"]


def _apply_hillshade(
    data: ImageData, azimuth: int = 315, angle_altitude: float = 45, buffer: int = 3
) -> ImageData:
    """Apply hillshade to a single ImageData."""
    x, y = numpy.gradient(data.array[0])
    slope = numpy.pi / 2.0 - numpy.arctan(numpy.sqrt(x * x + y * y))
    aspect = numpy.arctan2(-x, y)
    azimuthrad = azimuth * numpy.pi / 180.0
    altituderad = angle_altitude * numpy.pi / 180.0
    shaded = numpy.sin(altituderad) * numpy.sin(slope) + numpy.cos(
        altituderad
    ) * numpy.cos(slope) * numpy.cos(azimuthrad - aspect)
    datahs = 255 * (shaded + 1) / 2
    datahs[datahs < 0] = 0  # set hillshade values to min of 0.

    bounds = data.bounds
    datahs = datahs[buffer:-buffer, buffer:-buffer]

    window = windows.Window(
        col_off=buffer,
        row_off=buffer,
        width=datahs.shape[1],
        height=datahs.shape[0],
    )
    bounds = windows.bounds(window, data.transform)

    return ImageData(
        datahs.astype(numpy.uint8),
        assets=data.assets,
        crs=data.crs,
        bounds=bounds,
        band_names=["hillshade"],
    )


def hillshade(
    data: RasterStack, azimuth: int = 315, angle_altitude: float = 45, buffer: int = 3
) -> RasterStack:
    """Create hillshade from DEM dataset.

    Args:
        data: RasterStack to process
        azimuth: Azimuth of the light source in degrees
        angle_altitude: Altitude of the light source in degrees
        buffer: Number of pixels to use as a buffer

    Returns:
        RasterStack with hillshade results
    """
    # Apply hillshade to each item in the stack
    result = {}
    for key, img_data in data.items():
        result[key] = _apply_hillshade(img_data, azimuth, angle_altitude, buffer)

    return RasterStack.from_images(result)
