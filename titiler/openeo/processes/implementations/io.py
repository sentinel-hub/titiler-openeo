"""titiler.openeo.processes."""

from datetime import datetime, timezone
from typing import Dict, Optional, Union

import numpy
import pystac
from pystac.extensions.projection import ProjectionExtension
from rio_tiler.constants import MAX_THREADS, WGS84_CRS
from rio_tiler.io import COGReader
from rio_tiler.tasks import create_tasks

from ...reader import _reader
from ...settings import SigningSettings
from ...signing import get_signer, stamp_signer_key
from .data_model import RasterStack
from .formats import SaveResultData, encode

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
    # Unlike every other read path, this one has no catalogue behind it:
    # `load_url` is a plain process implementation running inside an
    # already-evaluating graph, so there is no ingest step to stamp its item and
    # no `LoadCollection` holding the deployment's choice. It reads that choice
    # straight from settings instead (docs/adr/0005-asset-href-signing.md S2.6).
    # The signer still returns any href it has nothing to add to untouched, so a
    # user-supplied URL cannot widen what gets signed.
    signer_key = SigningSettings().asset_signer or None

    # Get metadata from COG to build the item and set dimensions and CRS. This
    # open is outside any reader, so it signs the href itself.
    signer = get_signer(signer_key)
    with COGReader(signer(url) if signer else url) as cog:
        cog_width = cog.dataset.width
        cog_height = cog.dataset.height
        cog_crs = cog.dataset.crs
        cog_bounds = tuple(float(x) for x in cog.bounds)
        geographic_bounds = [float(x) for x in cog.get_geographic_bounds(WGS84_CRS)]
        cog_transform = list(cog.dataset.transform)[:6]

    # Build a valid STAC item for the COG, so `_reader` can parse it into the
    # `pystac.Item` that `SimpleSTACReader` needs. The `proj:*` fields let the
    # reader use the native grid; without them it takes the bbox as WGS84.
    west, south, east, north = geographic_bounds
    item = pystac.Item(
        id="cog",
        geometry={
            "type": "Polygon",
            "coordinates": [
                [
                    [west, south],
                    [east, south],
                    [east, north],
                    [west, north],
                    [west, south],
                ]
            ],
        },
        bbox=geographic_bounds,
        datetime=datetime.now(timezone.utc),
        properties={},
    )
    # No media type: any raster GDAL opens is accepted, not only COGs.
    item.add_asset("data", pystac.Asset(href=url))
    proj = ProjectionExtension.ext(item, add_if_missing=True)
    proj.shape = [cog_height, cog_width]
    proj.transform = cog_transform
    if cog_crs.to_epsg():
        proj.epsg = cog_crs.to_epsg()
    else:
        proj.wkt2 = cog_crs.to_wkt()

    # Stamped so the read path resolves the same credential as the open above.
    item_dict = stamp_signer_key(item.to_dict(), signer_key)

    # Create the tasks, read on the native grid of the COG
    tasks = create_tasks(
        _reader,
        [item_dict],
        MAX_THREADS,
        cog_bounds,
        assets=["data"],
        bounds_crs=cog_crs,
        dst_crs=cog_crs,
        width=cog_width,
        height=cog_height,
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


def save_result(
    data: Union[numpy.ndarray, numpy.ma.MaskedArray, RasterStack, dict],
    format: str,
    options: Optional[Dict] = None,
) -> SaveResultData:
    """Save Result.

    Args:
        data: The result to save: a raster (RasterStack, numpy array), a vector
            cube (GeoJSON FeatureCollection) or other data.
        format: Output format name or alias, case-insensitive. See
            `formats.OUTPUT_FORMATS`.
        options: Additional rendering options

    Returns:
        SaveResultData: the encoded bytes and their media type.

    Raises:
        FormatUnsuitable: If the format is unknown or can't store the data.
    """
    return encode(data, format, options)
