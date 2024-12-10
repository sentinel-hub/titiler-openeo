"""utils."""

from pyproj.crs import CRS as projCRS
from pyproj.enums import WktVersion
from rasterio.crs import CRS as rioCRS
from rasterio.env import GDALVersion


def to_rasterio_crs(crs: projCRS) -> rioCRS:
    """Convert a pyproj CRS to a rasterio CRS"""
    if GDALVersion.runtime().major < 3:
        return rioCRS.from_wkt(crs.to_wkt(WktVersion.WKT1_GDAL))
    else:
        return rioCRS.from_wkt(crs.to_wkt())
