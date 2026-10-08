"""Sentinel-2 L2A BOA scale/offset from the product metadata (`MTD_MSIL2A.xml`).

Since processing baseline 04.00 (2022-01-25), Sentinel-2 L2A reflectance is

    reflectance = (DN + BOA_ADD_OFFSET[band]) / BOA_QUANTIFICATION_VALUE

with `BOA_ADD_OFFSET` = -1000 today; earlier baselines have no offset. Both
values are in the product's `MTD_MSIL2A.xml`, which every catalogue this
project targets publishes as a STAC asset (`product-metadata` on Planetary
Computer, `product_metadata` on CDSE and Earth Search). Planetary Computer
publishes neither in STAC nor in the COG header
(microsoft/PlanetaryComputer#134), so this file is the only source there.

Verified live on 2026-10-08 against Planetary Computer products of baselines
02.12, 03.00 and 05.13: all share the layout parsed here, under
`General_Info/Product_Image_Characteristics`.

Parsed with `defusedxml` and matched by local tag name, for the same reasons
as `sentinel2/tile_metadata.py`.
"""

import re
from threading import Condition
from typing import Any, Dict, Mapping, Optional
from xml.etree.ElementTree import Element

from cachetools import LRUCache, cached
from cachetools.keys import hashkey
from defusedxml import ElementTree as ET

from ..sar.fetcher import AssetFetcher, get_default_fetcher
from ..sentinel2.tile_metadata import _local_name
from ..settings import Sentinel2Settings
from .registry import ProductScaleOffset, ScaleOffsetPlugin

__all__ = [
    "SENTINEL2_BOA",
    "parse_product_scale_offset",
    "get_product_scale_offset",
    "band_key",
]

_settings = Sentinel2Settings()

#: Non-reflectance bands whose quantification value is in the same file.
_QUANTIFIED_PRODUCTS = {
    "AOT": "AOT_QUANTIFICATION_VALUE",
    "WVP": "WVP_QUANTIFICATION_VALUE",
}

_BAND_NAME = re.compile(r"B0?(\d{1,2}A?)", re.IGNORECASE)


def _iter_named(root: Element, name: str):
    return (e for e in root.iter() if _local_name(e.tag) == name)


def _first_float(root: Element, name: str) -> Optional[float]:
    for e in _iter_named(root, name):
        if e.text and e.text.strip():
            return float(e.text)
    return None


def parse_product_scale_offset(xml_bytes: bytes) -> ProductScaleOffset:
    """Parse `MTD_MSIL2A.xml` into ``{"B4": (scale, offset), "AOT": ...}``.

    Band keys are the ESA physical band names (`B1` ... `B8A` ... `B12`).
    A band with no `BOA_ADD_OFFSET` (baseline < 04.00) gets offset 0.
    """
    root = ET.fromstring(xml_bytes)

    quantification = _first_float(root, "BOA_QUANTIFICATION_VALUE")
    if not quantification:
        raise ValueError("MTD_MSIL2A.xml has no BOA_QUANTIFICATION_VALUE")

    offsets: Dict[str, float] = {}
    for e in _iter_named(root, "BOA_ADD_OFFSET"):
        if (band_id := e.attrib.get("band_id")) is not None and e.text:
            offsets[band_id] = float(e.text)

    pairs: ProductScaleOffset = {}
    for e in _iter_named(root, "Spectral_Information"):
        band_id, physical = e.attrib.get("bandId"), e.attrib.get("physicalBand")
        if band_id is None or not physical:
            continue
        pairs[physical.upper()] = (
            1.0 / quantification,
            offsets.get(band_id, 0.0) / quantification,
        )
    if not pairs:
        raise ValueError("MTD_MSIL2A.xml has no Spectral_Information band list")

    for product, tag in _QUANTIFIED_PRODUCTS.items():
        if value := _first_float(root, tag):
            pairs[product] = (1.0 / value, 0.0)

    return pairs


def band_key(asset: Mapping[str, Any]) -> Optional[str]:
    """The physical band of a data asset, from its `eo:bands`/`bands` name.

    ``B04`` -> ``B4``, ``B8A`` -> ``B8A``, ``AOT`` -> ``AOT``. All three
    catalogues name the band this way, whatever the asset key (`B04`, `red`,
    `B04_10m`). Anything else (e.g. `SCL`) -> None, so the plugin leaves it.
    """
    bands = asset.get("eo:bands") or asset.get("bands") or []
    if not bands or not isinstance(bands[0], Mapping):
        return None
    name = str(bands[0].get("name") or "").upper()
    if name in _QUANTIFIED_PRODUCTS:
        return name
    if match := _BAND_NAME.fullmatch(name):
        return f"B{match.group(1).upper()}"
    return None


_product_cache: LRUCache = LRUCache(maxsize=_settings.product_metadata_cache_maxsize)
_product_cache_condition = Condition()


@cached(
    _product_cache,
    key=lambda href, fetch_href=None, fetcher=None: hashkey(href),
    condition=_product_cache_condition,
)
def get_product_scale_offset(
    href: str,
    fetch_href: Optional[str] = None,
    fetcher: Optional[AssetFetcher] = None,
) -> ProductScaleOffset:
    """Fetch and parse a product's `MTD_MSIL2A.xml`, cached by ``href``.

    ``href`` is the unsigned href and is the cache key; ``fetch_href`` is the
    href actually fetched (e.g. with a SAS token). Keying on the unsigned href
    keeps the entry across token renewals: the file never changes.
    """
    fetcher = fetcher or get_default_fetcher()
    return parse_product_scale_offset(fetcher.fetch(fetch_href or href))


SENTINEL2_BOA = ScaleOffsetPlugin(
    name="sentinel2-boa",
    collection=re.compile(r"sentinel-2-l2a"),
    asset=re.compile(r"product[_-]metadata"),
    media_types=frozenset({"application/xml", "text/xml"}),
    roles=frozenset({"metadata"}),
    load=get_product_scale_offset,
    band_key=band_key,
)
