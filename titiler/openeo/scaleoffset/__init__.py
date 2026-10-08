"""Scale/offset plugins (docs/adr/0009-scale-offset-sources.md)."""

from typing import Dict

from .registry import ProductScaleOffset, ScaleOffsetPlugin, find_metadata_asset
from .sentinel2 import SENTINEL2_BOA

__all__ = [
    "PLUGINS",
    "ProductScaleOffset",
    "ScaleOffsetPlugin",
    "find_metadata_asset",
]

#: Plugin name -> plugin. Every key must also be in
#: `settings.ScaleOffsetSource` (enforced by a test).
PLUGINS: Dict[str, ScaleOffsetPlugin] = {SENTINEL2_BOA.name: SENTINEL2_BOA}
