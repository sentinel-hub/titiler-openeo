"""utils."""

from datetime import datetime
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


def _props_to_timestamp(props: Dict) -> str:
    """Extract timestamp from STAC item properties.

    This is the new preferred function name for temporal metadata extraction.
    Provides the same functionality as _props_to_datename but with clearer naming.
    """
    if d := props["datetime"]:
        return d

    start_date = props["start_datetime"]
    end_date = props["end_datetime"]
    return start_date if start_date else end_date


def _props_to_datetime(props: Dict) -> datetime:
    """Extract datetime object from STAC item properties.

    Converts ISO format timestamp strings to datetime objects.
    """
    timestamp_str = _props_to_timestamp(props)

    # Handle various ISO format variations
    # Remove 'Z' suffix if present and replace with '+00:00' for proper parsing
    if timestamp_str.endswith("Z"):
        timestamp_str = timestamp_str[:-1] + "+00:00"

    try:
        return datetime.fromisoformat(timestamp_str)
    except ValueError:
        # Fallback for simpler formats without timezone info
        try:
            # Try parsing without microseconds and timezone
            return datetime.strptime(timestamp_str, "%Y-%m-%dT%H:%M:%S")
        except ValueError:
            # Try with just date
            return datetime.strptime(timestamp_str, "%Y-%m-%d")
