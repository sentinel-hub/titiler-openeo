"""Band sources: derive cube bands from non-raster STAC assets.

See docs/adr/0002-band-sources.md.
"""

from .registry import BandSource, derive_bands
from .sources import BAND_SOURCES

__all__ = ["BandSource", "derive_bands", "BAND_SOURCES"]
