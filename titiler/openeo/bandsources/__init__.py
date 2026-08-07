"""Band sources: derive cube bands from non-raster STAC assets.

See docs/adr/0002-band-sources.md.
"""

from .registry import BandSource, ResolvedBand, derive_bands, resolve_band
from .sources import BAND_SOURCES

__all__ = [
    "BandSource",
    "ResolvedBand",
    "derive_bands",
    "resolve_band",
    "BAND_SOURCES",
]
