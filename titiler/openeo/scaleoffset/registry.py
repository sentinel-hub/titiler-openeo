"""Scale/offset plugins: per-mission sources of band scale/offset.

Some catalogues publish raw DN with no usable scale/offset in STAC or in the
COG header, while the mission's own product metadata carries the true values
(e.g. Sentinel-2 L2A `MTD_MSIL2A.xml` on Microsoft Planetary Computer). A
plugin reads that metadata and returns a (scale, offset) pair per band.

A plugin is matched to an item the same way a band source is
(`bandsources/registry.py`): by collection id, then by the key, media type and
role of one metadata asset. It holds no provider names: which catalogue needs
it is the deployment's choice, made by listing the plugin's name in
`ProcessingSettings.scale_offset_sources` / `scale_offset_collections`
(docs/adr/0009-scale-offset-sources.md).
"""

import re
from dataclasses import dataclass
from typing import Any, Callable, Dict, FrozenSet, Mapping, Optional, Tuple

import pystac

__all__ = ["ScaleOffsetPlugin", "ProductScaleOffset", "find_metadata_asset"]

#: Physical band name (as the plugin's `band_key` returns it) -> (scale, offset).
ProductScaleOffset = Dict[str, Tuple[float, float]]


@dataclass(frozen=True)
class ScaleOffsetPlugin:
    """One source of per-band scale/offset, read from a metadata asset."""

    #: The name a deployment lists in its scale/offset sources.
    name: str
    #: `search`ed against the item's collection id.
    collection: re.Pattern
    #: `fullmatch`ed against the metadata asset's key.
    asset: re.Pattern
    media_types: FrozenSet[str]
    roles: FrozenSet[str]
    #: ``load(href, fetch_href, fetcher)`` -> per-band pairs. ``href`` is the
    #: unsigned href (a stable cache key); ``fetch_href`` is the one to fetch
    #: (e.g. signed). Raises when the metadata cannot be fetched or parsed.
    load: Callable[[str, str, Any], ProductScaleOffset]
    #: A data asset's STAC fields -> its key in the parsed pairs, or None when
    #: the plugin has nothing to say about that asset.
    band_key: Callable[[Mapping[str, Any]], Optional[str]]


def find_metadata_asset(
    plugin: ScaleOffsetPlugin, item: pystac.Item
) -> Optional[pystac.Asset]:
    """The asset of ``item`` that ``plugin`` reads, or None if it does not apply."""
    if not plugin.collection.search(item.collection_id or ""):
        return None
    for key, asset in item.assets.items():
        if not plugin.asset.fullmatch(key):
            continue
        if asset.media_type not in plugin.media_types:
            continue
        if not plugin.roles.intersection(asset.roles or []):
            continue
        return asset
    return None
