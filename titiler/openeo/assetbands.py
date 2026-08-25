"""Bands published *inside* one STAC asset.

Most catalogues give each band its own asset, so a band name is an asset key and
nothing here applies. Some publish one asset holding many bands and describe them
in the asset's ``bands`` array -- EOPF's Sentinel-2 ``reflectance`` asset carries
all twelve, each with its own ``gsd`` and spectral metadata.

This module is the one place that maps such a band name back to
``(asset key, band within it)``. It is deliberately shared by all four callers
that need the mapping -- collection discovery, band summaries, the read path and
resolution estimation -- because a backend that advertises a band name it cannot
read is worse than one that advertises nothing.

Unlike :mod:`titiler.openeo.bandsources`, which matches hand-written rules
against collection ids, everything here is derived from the item's own metadata.
There is no registry: a catalogue that describes its assets gets this for free.

Compatibility
-------------

Three rules keep existing catalogues' band names exactly as they were, and all
are load-bearing rather than cautious:

* An asset with **no** ``bands`` array keeps its asset key (EOPF's own
  ``AOT_10m``/``SCL_20m``/``WVP_10m`` are this shape).
* An asset with **exactly one** band keeps its asset key too. Single-band assets
  routinely name the band differently from the key -- CDSE publishes
  ``B02_10m`` holding a band named ``B02``, earth-search publishes ``blue``
  holding ``B02`` -- so expanding them would silently rename bands that saved
  process graphs and services already reference, and would collapse the several
  resolutions CDSE distinguishes in the key.
* An asset carrying a **rendering role** -- ``visual``, ``overview`` or
  ``thumbnail`` -- keeps its asset key too, however many bands it declares. A
  true-colour ``TCI`` asset lists ``[B04, B03, B02]`` in ``eo:bands`` to
  describe one 3-channel rendered image, not three bands a caller could
  request separately; its ``roles`` say so directly. This is a STAC-standard
  signal (unlike the datacube extension's ``cube:variables``, which an
  otherwise perfectly ordinary multi-band data asset is not obliged to carry)
  and the one already used elsewhere in this codebase to draw the same
  distinction (``stacapi.py``'s own ``"data" in roles`` check).

Any other asset with two or more declared bands expands -- publishing a
``bands`` array at all is itself the catalogue's declaration that these are
worth describing individually, and there is no more reliable signal to
require on top of that without excluding real catalogues that simply never
touch the datacube extension.
"""

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

__all__ = [
    "AssetBandFacts",
    "ResolvedAssetBand",
    "asset_band_facts",
    "resolve_asset_bands",
]

#: ``(asset_key, bands)`` for one asset, where ``bands`` is its STAC ``bands``
#: (or legacy ``eo:bands``) array -- the shape both entry points can produce,
#: from an item's ``assets`` or a collection's ``item_assets``.
AssetBandFacts = Tuple[str, Sequence[Mapping[str, Any]]]


@dataclass(frozen=True)
class ResolvedAssetBand:
    """Which asset holds one band, and what it is called inside that asset."""

    #: The asset to open, e.g. ``"reflectance"``.
    asset_key: str
    #: The band's name *within* that asset, e.g. ``"blue"``. Passed straight to
    #: rio-tiler's per-asset ``bands`` option
    #: (`SimpleSTACReader._get_options`/`_get_asset_info`), so this must be
    #: resolved with the identical precedence that function uses to look a name
    #: back up -- see `_band_display_name`. Using anything else here would
    #: advertise a name `_get_options` cannot itself resolve.
    band_name: str
    #: The band's own entry from the asset's ``bands`` array. Carries the
    #: per-band ``gsd`` and spectral fields that discovery advertises and
    #: resolution estimation needs -- a multi-band asset's own ``gsd`` describes
    #: only its finest band, so it is the wrong number for the others.
    metadata: Mapping[str, Any]


#: Asset roles that mark a rendering/preview product rather than independently
#: addressable data bands -- see the module docstring's compatibility rules.
_RENDERING_ROLES = frozenset({"visual", "overview", "thumbnail"})


def _bands_of(asset: Any) -> Sequence[Mapping[str, Any]]:
    """The ``bands`` array of one asset that is safe to treat as independently
    addressable, whether the asset is a dict or a pystac Asset/AssetDefinition.

    Excludes an asset carrying a rendering role -- see the module docstring's
    compatibility rules. ``roles`` is handled separately from ``bands`` because
    a real ``pystac.Asset`` exposes it as a first-class attribute, not inside
    ``extra_fields`` (unlike a plain dict, e.g. an ``item_assets`` entry, where
    both live at the top level alongside everything else).
    """
    if isinstance(asset, Mapping):
        source: Mapping[str, Any] = asset
        roles = source.get("roles")
    else:
        source = getattr(asset, "extra_fields", None) or {}
        roles = getattr(asset, "roles", None)

    if _RENDERING_ROLES & set(roles or ()):
        return []

    return source.get("bands") or source.get("eo:bands") or []


def _band_display_name(band: Mapping[str, Any]) -> Optional[str]:
    """A band's name, by the same precedence `_get_options` resolves it with:
    ``eo:common_name`` (there is no standard precedence between it and
    ``name`` in the STAC spec -- rio-tiler's convention, kept verbatim so a
    resolved name is always a name `_get_options` can look back up), then the
    legacy ``common_name``, then the band's own ``name``.
    """
    return band.get("eo:common_name") or band.get("common_name") or band.get("name")


def asset_band_facts(assets: Mapping[str, Any]) -> List[AssetBandFacts]:
    """Build this module's input from an item's ``assets`` or a collection's
    ``item_assets``, accepting dicts and pystac objects alike."""
    return [(key, _bands_of(asset)) for key, asset in assets.items()]


def resolve_asset_bands(
    assets: Iterable[AssetBandFacts],
) -> Dict[str, ResolvedAssetBand]:
    """Map each band published inside a multi-band asset to where it lives.

    Assets keeping their key (see the module docstring) are absent from the
    result, so a caller's ``band_name in resolved`` is exactly the question "is
    this name something other than an asset key".

    When two multi-band assets publish the same band name, every occurrence of
    that name is qualified as ``{asset_key}_{band_name}`` rather than one of
    them silently winning. Names unique across the item are left bare, so the
    common case reads as the catalogue wrote it.
    """
    candidates: List[Tuple[str, ResolvedAssetBand]] = []
    seen: Dict[str, int] = {}

    for asset_key, bands in assets:
        if len(bands) < 2:
            continue

        for band in bands:
            name = _band_display_name(band)
            if not name:
                continue
            candidates.append(
                (name, ResolvedAssetBand(asset_key, name, band)),
            )
            seen[name] = seen.get(name, 0) + 1

    return {
        (name if seen[name] == 1 else f"{resolved.asset_key}_{name}"): resolved
        for name, resolved in candidates
    }


def resolve_asset_band(
    band_name: str,
    assets: Iterable[AssetBandFacts],
) -> Optional[ResolvedAssetBand]:
    """``resolve_asset_bands`` for a single name, or ``None`` if it is not one."""
    return resolve_asset_bands(assets).get(band_name)
