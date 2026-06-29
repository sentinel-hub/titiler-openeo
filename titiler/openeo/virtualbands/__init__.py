"""Virtual bands plugin mechanism for titiler-openeo.

Virtual bands are computed at read time from STAC item metadata and/or real band
pixel values, and bound to collections through configuration. See
:mod:`titiler.openeo.virtualbands.registry` for the binding mechanism and
:mod:`titiler.openeo.virtualbands.base` for the plugin interface.
"""

from .base import BandMetadata, VirtualBandPlugin, band_array
from .registry import ENTRY_POINT_GROUP, SplitBands, VirtualBandRegistry

__all__ = [
    "BandMetadata",
    "VirtualBandPlugin",
    "band_array",
    "VirtualBandRegistry",
    "SplitBands",
    "ENTRY_POINT_GROUP",
]
