"""Band sources: derive cube bands from non-raster STAC assets.

See docs/adr/0002-band-sources.md (Sentinel-1) and
docs/adr/0004-sentinel2-view-sun-angle-bands.md (Sentinel-2).
"""

from .registry import (
    BandSource,
    ResolvedBand,
    SiblingCandidateFacts,
    derive_bands,
    pick_nominal_sibling_by_resolution,
    resolve_band,
)
from .sentinel2_sources import BAND_SOURCES_S2 as _BAND_SOURCES_S2
from .sources import BAND_SOURCES as _BAND_SOURCES_S1

#: The shipped registry, merged across every band-source family. `reader.py`
#: and `stacapi.py` treat this as "the" registry -- neither imports a
#: per-family list directly, so this is the one place new families merge in.
BAND_SOURCES = [*_BAND_SOURCES_S1, *_BAND_SOURCES_S2]

__all__ = [
    "BandSource",
    "ResolvedBand",
    "SiblingCandidateFacts",
    "derive_bands",
    "resolve_band",
    "pick_nominal_sibling_by_resolution",
    "BAND_SOURCES",
]
