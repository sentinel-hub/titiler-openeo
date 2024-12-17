"""titiler.openeo.processes dem."""

import numpy
from rasterio import windows

from .data_model import ImageData

__all__ = ["hillshade"]


def hillshade(data: ImageData, azimuth: int, angle_altitude: float, buffer: int):
    """Create hillshade from DEM dataset."""
    x, y = numpy.gradient(data.array[0])
    slope = numpy.pi / 2.0 - numpy.arctan(numpy.sqrt(x * x + y * y))
    aspect = numpy.arctan2(-x, y)
    azimuth = 360 - azimuth
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
