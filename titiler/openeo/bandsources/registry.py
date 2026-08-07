"""Match STAC-observable facts to derived band names.

See docs/adr/0002-band-sources.md. A collection's non-raster assets (annotation
XML, geolocation grids, ...) can carry data that belongs on the cube as bands,
but nothing in the asset itself says which asset gives which band -- that
mapping is a property of the collection's convention (e.g. Sentinel-1 GRD's
`schema-calibration-<pol>`/`schema-noise-<pol>` naming), so it is looked up in
a small, in-code registry rather than inferred.

Media type and role alone are not enough to tell these assets apart: on every
target catalogue, `schema-calibration-*`, `schema-noise-*`, `schema-product-*`
and the SAFE manifest are all `application/xml` with role `metadata` (ADR
0002 S1.2). The asset *key* is therefore part of the match, and its regex's
named groups double as the parameters for the derived band names -- e.g. a
group named `pol` both discriminates `schema-noise-vv` from
`schema-noise-vh` and lets one entry describe every polarisation at once.

Two questions are asked of this registry, from two different places:

* ``derive_bands`` -- "which band names does this collection/item make
  available" -- discovery (`stacapi.getdimensions`, issue #348 increment 1),
  over collection-level ``item_assets``.
* ``resolve_band`` -- "which asset, read by which reader, produces this one
  band" -- production (`SimpleSTACReader`, increment 2), over one item's own
  ``assets``. A ``BandSource`` with no ``reader`` set is still visible to
  ``derive_bands`` (discovery can advertise a band before its reader exists,
  which is exactly increment 1's shipped state) but invisible to
  ``resolve_band`` (nothing can produce it yet).
"""

import re
from dataclasses import dataclass
from typing import (
    FrozenSet,
    Iterable,
    Iterator,
    List,
    Optional,
    Sequence,
    Set,
    Tuple,
    Type,
)

from rio_tiler.io.base import BaseReader

__all__ = ["BandSource", "ResolvedBand", "derive_bands", "resolve_band"]

#: One (asset_key, media_type, roles) triple as found on a STAC item or
#: collection's item_assets -- the common shape both entry points take.
AssetFacts = Tuple[str, Optional[str], Sequence[str]]


@dataclass(frozen=True)
class BandSource:
    """One rule: which assets give rise to which band names, and how to read them.

    ``collection`` and ``asset`` are pre-compiled patterns so a registry with
    many entries does not recompile them per lookup. ``collection`` is matched
    with :meth:`re.Pattern.search` (a substring match, since deployments may
    suffix or version a collection id); ``asset`` is matched with
    :meth:`re.Pattern.fullmatch` against the whole asset key, since asset keys
    are exact tokens, not free text.

    ``bands`` are ``(name_template, quantity)`` pairs. ``name_template`` is
    formatted with the ``asset`` match's named groups
    (``str.format(**match.groupdict())``), so ``"{pol}_noise_lut"`` resolves
    to ``"vv_noise_lut"`` when ``asset`` matched with ``pol="vv"``.
    ``quantity`` is opaque to this module -- it is threaded through to
    ``reader`` (as its ``quantity`` constructor kwarg) so one asset can
    produce several distinctly-computed bands without needing a reader
    subclass per quantity, e.g. one calibration annotation's four LUT
    vectors plus the incidence angle (ADR 0002 S2.5) all share
    ``CalibrationBandReader``, dispatching on ``quantity`` alone.

    ``sibling`` is the same kind of template for the raster asset a reader
    needs alongside the matched one (e.g. the measurement asset, for GCPs) --
    ``None`` if a band needs none.

    ``reader`` is the ``BaseReader`` subclass that produces these bands at
    read time. Left ``None`` for a band that discovery should advertise
    before its reader exists (increment 1's shipped state for every band this
    registry currently described) -- ``resolve_band`` treats that the same as
    "no source matched".
    """

    collection: "re.Pattern[str]"
    media_types: FrozenSet[str]
    roles: FrozenSet[str]
    asset: "re.Pattern[str]"
    bands: Tuple[Tuple[str, str], ...]
    sibling: Optional[str] = None
    reader: Optional[Type[BaseReader]] = None


@dataclass(frozen=True)
class ResolvedBand:
    """Which asset (and optionally sibling asset) produces one derived band."""

    #: The matched asset's key, e.g. ``"schema-noise-vv"``.
    asset_key: str
    #: The sibling raster asset's key a reader needs alongside it, e.g.
    #: ``"vv"`` -- ``None`` if the band needs no sibling.
    sibling_key: Optional[str]
    #: Which quantity this specific band is, from the matching
    #: ``BandSource.bands`` entry -- e.g. ``"sigma_nought"``. Opaque to this
    #: module; passed straight through to ``reader``.
    quantity: Optional[str]
    reader: Type[BaseReader]


def _iter_matches(
    collection_id: str,
    assets: Iterable[AssetFacts],
    sources: Sequence[BandSource],
) -> Iterator[Tuple[BandSource, str, dict, List[str]]]:
    """Yield ``(source, asset_key, match_groups, band_names)`` for every match."""
    assets = list(assets)

    for source in sources:
        if not source.collection.search(collection_id):
            continue

        for asset_key, media_type, roles in assets:
            if media_type not in source.media_types:
                continue
            if not source.roles & set(roles or ()):
                continue

            match = source.asset.fullmatch(asset_key)
            if not match:
                continue

            groups = match.groupdict()
            band_names = [name.format(**groups) for name, _quantity in source.bands]
            yield source, asset_key, groups, band_names


def derive_bands(
    collection_id: str,
    assets: Iterable[AssetFacts],
    sources: Sequence[BandSource],
) -> List[str]:
    """Return the sorted, de-duplicated derived band names for a collection.

    Args:
        collection_id: The STAC collection id.
        assets: ``(asset_key, media_type, roles)`` triples -- one per entry in
            the collection's ``item_assets``. ``media_type`` and ``roles``
            mirror the STAC fields of the same name (``roles`` may be empty).
        sources: The registry entries to match against, e.g.
            :data:`titiler.openeo.bandsources.sources.BAND_SOURCES`. Accepting
            this as a parameter (rather than importing the shipped registry
            directly) keeps this function trivially testable against
            synthetic entries.

    Returns:
        Sorted band names with no duplicates. Sorted rather than left as a set
        so callers get a deterministic ``cube:dimensions`` order (issue #280).
        Includes bands whose ``BandSource.reader`` is still ``None`` --
        discovery may advertise a band before its reader exists.
    """
    names: Set[str] = set()
    for _source, _asset_key, _groups, band_names in _iter_matches(
        collection_id, assets, sources
    ):
        names.update(band_names)
    return sorted(names)


def resolve_band(
    collection_id: str,
    band_name: str,
    assets: Iterable[AssetFacts],
    sources: Sequence[BandSource],
) -> Optional[ResolvedBand]:
    """Find the asset (and reader) that produces ``band_name`` for one item.

    The mirror of ``derive_bands``: that function answers "which band names
    does this item/collection make available"; this one answers "which
    asset, read by which reader, actually produces this one band" -- what
    ``SimpleSTACReader`` needs at request time to resolve a derived band name
    into a pseudo-asset (ADR 0002 S2.3).

    Args:
        collection_id: The STAC collection id.
        band_name: The requested (derived) band name.
        assets: ``(asset_key, media_type, roles)`` triples for one item's own
            ``assets`` (not a collection's ``item_assets`` template -- these
            need real hrefs).
        sources: The registry entries to match against.

    Returns:
        ``None`` if no source matches, or if the matching source's ``reader``
        is not yet set (discovery can advertise a band ahead of its reader;
        this function cannot resolve one).
    """
    for source, asset_key, groups, band_names in _iter_matches(
        collection_id, assets, sources
    ):
        if band_name not in band_names:
            continue
        if source.reader is None:
            continue

        quantity = source.bands[band_names.index(band_name)][1]
        sibling_key = source.sibling.format(**groups) if source.sibling else None
        return ResolvedBand(
            asset_key=asset_key,
            sibling_key=sibling_key,
            quantity=quantity,
            reader=source.reader,
        )

    return None
