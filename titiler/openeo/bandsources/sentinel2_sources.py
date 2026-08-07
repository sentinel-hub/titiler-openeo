"""Shipped Sentinel-2 view/sun angle band-source entries.

See docs/adr/0004-sentinel2-view-sun-angle-bands.md. Verified live against
CDSE, Earth Search and Planetary Computer (2026-08-07): all three publish
`granule_metadata`/`granule-metadata` (spelling differs -- CDSE/Earth Search
use an underscore, Planetary Computer a hyphen) as `application/xml` with
role `metadata`, and all three currently use the collection id
`sentinel-2-l2a`.

Both entries match the *same* asset -- one `MTD_TL.xml` backs both the
mean-across-bands viewing angles and the per-pixel sun-angle grid, read by
two different reader classes (`_iter_matches` in `registry.py` already
evaluates every source against every asset independently; this is not new
behaviour, S1 simply never had two sources share one asset key before).
"""

import re

from .registry import BandSource, pick_nominal_sibling_by_resolution
from .sentinel2_readers import SunAngleGridReader, ViewAngleMeanReader

__all__ = ["BAND_SOURCES_S2"]

#: `search` (not `fullmatch`) so a future deployment-specific suffix still
#: matches, mirroring sources.py's own S1 collection pattern.
_S2_L2A_COLLECTION = re.compile(r"sentinel-2-l2a")

_XML = frozenset({"application/xml"})
_METADATA = frozenset({"metadata"})

#: CDSE/Earth Search spell this with an underscore, Planetary Computer with
#: a hyphen -- verified live, ADR 0004 S1.3.
_GRANULE_METADATA_ASSET = re.compile(r"granule[_-]metadata")

BAND_SOURCES_S2 = [
    # Mean_Viewing_Incidence_Angle_List has one ZENITH_ANGLE/AZIMUTH_ANGLE
    # pair per Sentinel-2 band, no pre-averaged value -- ViewAngleMeanReader
    # computes the mean-across-bands scalar itself.
    BandSource(
        collection=_S2_L2A_COLLECTION,
        media_types=_XML,
        roles=_METADATA,
        asset=_GRANULE_METADATA_ASSET,
        bands=(
            ("viewZenithMean", "zenith"),
            ("viewAzimuthMean", "azimuth"),
        ),
        sibling=pick_nominal_sibling_by_resolution,
        reader=ViewAngleMeanReader,
    ),
    # Sun_Angles_Grid is a real 23x23, 5000m-step spatial grid --
    # SunAngleGridReader bilinearly interpolates it at the destination grid.
    BandSource(
        collection=_S2_L2A_COLLECTION,
        media_types=_XML,
        roles=_METADATA,
        asset=_GRANULE_METADATA_ASSET,
        bands=(
            ("sunZenithAngles", "zenith"),
            ("sunAzimuthAngles", "azimuth"),
        ),
        sibling=pick_nominal_sibling_by_resolution,
        reader=SunAngleGridReader,
    ),
]
