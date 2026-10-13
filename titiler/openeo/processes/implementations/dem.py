"""titiler.openeo.processes dem."""

from typing import List, Optional, Tuple

import numpy
from rasterio import windows

from .data_model import ImageData, RasterStack

__all__ = ["aspect", "hillshade", "slope"]

# Mean Earth radius (IUGG), used to convert a geographic pixel size to metres.
EARTH_RADIUS = 6371008.8


def _pixel_size(img: ImageData) -> Tuple[numpy.ndarray, float]:
    """Return the pixel size in metres as ``(dx per row, dy)``.

    For a projected CRS the size comes from the bounds and is the same for all
    rows. For a geographic CRS the size in degrees is converted to metres on a
    sphere, and the east-west size shrinks with the cosine of the row latitude.
    """
    west, south, east, north = img.bounds
    res_x = (east - west) / img.width
    res_y = (north - south) / img.height
    if img.crs is not None and img.crs.is_geographic:
        metres_per_degree = numpy.pi / 180.0 * EARTH_RADIUS
        latitudes = north - (numpy.arange(img.height) + 0.5) * res_y
        dx = res_x * metres_per_degree * numpy.cos(numpy.deg2rad(latitudes))
        return dx, res_y * metres_per_degree
    return numpy.full(img.height, res_x), res_y


def _gradients(img: ImageData) -> Tuple[numpy.ndarray, numpy.ndarray, numpy.ndarray]:
    """Return the east and north elevation gradients of each band, and their mask.

    The gradients use central differences (``numpy.gradient``) with the pixel
    size in metres, so the elevation must be in metres too. The result has the
    grid of the input. A pixel is masked when it is on the 1-pixel border or
    when one of its neighbours is no-data, as with GDAL ``gdaldem``.
    """
    z = numpy.ma.masked_invalid(img.array.astype("float64")).filled(numpy.nan)
    dx, dy = _pixel_size(img)
    dz_drow, dz_dcol = numpy.gradient(z, axis=(1, 2))
    # Rows go from north to south, so the north gradient has the opposite sign.
    dz_dx = dz_dcol / dx[numpy.newaxis, :, numpy.newaxis]
    dz_dy = -dz_drow / dy

    mask = numpy.isnan(dz_dx) | numpy.isnan(dz_dy)
    mask[:, [0, -1], :] = True
    mask[:, :, [0, -1]] = True
    return dz_dx, dz_dy, mask


def _band_names(img: ImageData, suffix: str) -> List[str]:
    """Return the input band names with a suffix."""
    names = img.band_descriptions or [f"b{i + 1}" for i in range(img.count)]
    return [f"{name}_{suffix}" for name in names]


def _terrain_image(
    img: ImageData, values: numpy.ndarray, mask: numpy.ndarray, suffix: str
) -> ImageData:
    """Make a float32 ImageData on the input grid from terrain values."""
    band_names = _band_names(img, suffix)
    return ImageData(
        numpy.ma.MaskedArray(values.astype("float32"), mask=mask),
        assets=img.assets,
        crs=img.crs,
        bounds=img.bounds,
        cutline_mask=img.cutline_mask,
        metadata=img.metadata,
        band_names=band_names,
        band_descriptions=band_names,
    )


def _stack_band_names(data: RasterStack, suffix: str) -> Optional[List[str]]:
    """Return the stack-level band names with a suffix, if the stack has them."""
    if not data.band_names:
        return None
    return [f"{name}_{suffix}" for name in data.band_names]


def _apply_slope(img: ImageData) -> ImageData:
    """Compute the slope of each band of a single ImageData."""
    dz_dx, dz_dy, mask = _gradients(img)
    values = numpy.arctan(numpy.hypot(dz_dx, dz_dy))
    return _terrain_image(img, values, mask, "slope")


def _apply_aspect(img: ImageData) -> ImageData:
    """Compute the aspect of each band of a single ImageData."""
    dz_dx, dz_dy, mask = _gradients(img)
    # The downslope direction is the opposite of the gradient. Its azimuth,
    # clockwise from north, is atan2(east component, north component).
    with numpy.errstate(invalid="ignore"):
        values = numpy.mod(numpy.arctan2(-dz_dx, -dz_dy), 2 * numpy.pi)
    values = numpy.where((dz_dx == 0) & (dz_dy == 0), numpy.nan, values)
    return _terrain_image(img, values, mask, "aspect")


def slope(data: RasterStack) -> RasterStack:
    """Compute the slope of elevation data.

    The slope is in radians, relative to the horizontal plane. The elevation
    must be in metres. For a geographic CRS, the pixel size is converted to
    metres on a sphere.

    Args:
        data: RasterStack with elevation bands

    Returns:
        RasterStack with one ``<band>_slope`` band per input band, on the
        input grid. The 1-pixel border is no-data.
    """
    band_names = _stack_band_names(data, "slope")
    overrides = {"band_names": band_names} if band_names else {}
    return data.map_images(_apply_slope, **overrides)


def aspect(data: RasterStack) -> RasterStack:
    """Compute the aspect of elevation data.

    The aspect is the direction that the slope faces (the downslope
    direction), in radians clockwise from north, from 0 to 2π: north is 0,
    east is π/2, south is π and west is 3π/2. This is the GDAL ``gdaldem
    aspect`` convention. On flat terrain the aspect is NaN.

    Args:
        data: RasterStack with elevation bands

    Returns:
        RasterStack with one ``<band>_aspect`` band per input band, on the
        input grid. The 1-pixel border is no-data.
    """
    band_names = _stack_band_names(data, "aspect")
    overrides = {"band_names": band_names} if band_names else {}
    return data.map_images(_apply_aspect, **overrides)


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
        band_descriptions=["hillshade"],
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
