"""titiler.openeo.processes."""

from datetime import datetime
from typing import Any, Dict, Optional, Union

import numpy
from rio_tiler.constants import MAX_THREADS
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

    # Create a dummy STAC item for the COG, stamped so the read path resolves
    # the same credential the metadata open below uses.
    item: Dict[str, Any] = {
        "type": "Feature",
        "id": "cog",
        "bbox": None,  # Will be set from the COG metadata
        "properties": {},
        "assets": {
            "data": {
                "href": url,
                "type": "image/tiff; application=geotiff; profile=cloud-optimized",
            }
        },
    }
    stamp_signer_key(item, signer_key)

    # Get metadata from COG to set bbox, dimensions, and CRS. This open is
    # outside any reader, so it signs the href itself.
    signer = get_signer(signer_key)
    with COGReader(signer(url) if signer else url) as cog:
        item["bbox"] = [float(x) for x in cog.bounds]
        cog_width = cog.dataset.width
        cog_height = cog.dataset.height
        cog_crs = cog.dataset.crs
        cog_bounds = tuple(float(x) for x in cog.bounds)

    # Create the tasks
    tasks = create_tasks(
        _reader,
        [item],
        MAX_THREADS,
        item["bbox"],
        assets=["data"],
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
