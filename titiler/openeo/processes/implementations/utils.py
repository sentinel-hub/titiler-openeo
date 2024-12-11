"""utils."""

from typing import Dict

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


def _props_to_datename(props: Dict) -> str:
    if d := props["datetime"]:
        return d

    start_date = props["start_datetime"]
    end_date = props["end_datetime"]
    return start_date if start_date else end_date
