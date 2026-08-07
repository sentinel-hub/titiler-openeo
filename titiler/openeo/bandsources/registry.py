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
    Callable,
    FrozenSet,
    Iterable,
    Iterator,
    List,
    Optional,
    Sequence,
    Set,
    Tuple,
    Type,
    Union,
)

from rio_tiler.io.base import BaseReader

__all__ = [
    "BandSource",
    "ResolvedBand",
    "SiblingCandidateFacts",
    "derive_bands",
    "resolve_band",
    "pick_nominal_sibling_by_resolution",
]

#: One (asset_key, media_type, roles) triple as found on a STAC item or
#: collection's item_assets -- the common shape both entry points take.
AssetFacts = Tuple[str, Optional[str], Sequence[str]]

#: AssetFacts extended with a candidate's declared ground sample distance
#: (metres, e.g. STAC's `gsd` field) -- used only when a BandSource's
#: `sibling` is callable rather than a string template (ADR 0004 S2.1).
#: Kept separate from AssetFacts itself so every existing derive_bands/
#: resolve_band call site -- Sentinel-1's included -- is completely
#: unaffected; only a source with a callable `sibling` ever looks at this.
SiblingCandidateFacts = Tuple[str, Optional[str], Sequence[str], Optional[float]]

#: Media types that are real files but not raster bands (e.g. a SAFE
#: product zip) -- excluded from pick_nominal_sibling_by_resolution's
#: candidate pool, mirroring stacapi.py's own `_ARCHIVE_MEDIA_TYPES` filter
#: for the same reason.
_ARCHIVE_MEDIA_TYPES = frozenset({"application/zip"})


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

    ``sibling`` names the raster asset a reader needs alongside the matched
    one (e.g. the measurement asset, for GCPs) -- ``None`` if a band needs
    none. Two shapes:

    * a ``str`` template, formatted with the ``asset`` match's named groups
      (``"{pol}"`` -> ``"vv"``) -- correct whenever the sibling's name is
      itself a function of the matched asset key, as every Sentinel-1 entry
      is today.
    * a callable ``(sibling_candidates) -> Optional[str]``, for a source
      whose logical sibling has no name expressible as a fixed template --
      e.g. Sentinel-2's red-band-equivalent asset is spelled ``B04_10m``
      (CDSE), ``red`` (Earth Search) and ``B04`` (Planetary Computer) for the
      identical collection id (ADR 0004 S2.1). ``resolve_band`` calls it with
      the item's own candidate assets (richer than ``AssetFacts`` -- see
      ``SiblingCandidateFacts``) to pick a sibling key dynamically.

    Every Sentinel-1 entry uses the ``str`` form, so this changes nothing for
    them -- there is no third, blended case where one source needs both a
    match group and the item's other assets.

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
    sibling: Optional[
        Union[str, Callable[[Sequence[SiblingCandidateFacts]], Optional[str]]]
    ] = None
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
    sibling_candidates: Optional[Sequence[SiblingCandidateFacts]] = None,
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
        sibling_candidates: The item's own assets as ``SiblingCandidateFacts``
            (adds declared ``gsd`` to each), passed to a matching source's
            ``sibling`` when it is callable rather than a string template
            (ADR 0004 S2.1). Optional and unused by every Sentinel-1 entry,
            whose ``sibling`` is a string.

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
        sibling_key: Optional[str]
        if isinstance(source.sibling, str):
            sibling_key = source.sibling.format(**groups)
        elif callable(source.sibling) and sibling_candidates is not None:
            sibling_key = source.sibling(sibling_candidates)
        else:
            sibling_key = None
        return ResolvedBand(
            asset_key=asset_key,
            sibling_key=sibling_key,
            quantity=quantity,
            reader=source.reader,
        )

    return None


def pick_nominal_sibling_by_resolution(
    candidates: Sequence[SiblingCandidateFacts],
) -> Optional[str]:
    """Pick a representative real raster asset to lend a band a resolution
    and a mask to inherit (ADR 0002 S2.4 rules 2-3), for a source whose
    sibling has no name expressible as a fixed template (ADR 0004 S2.1).

    Picks the smallest declared ``gsd`` among ``role=data``, non-archive
    candidates, tie-broken alphabetically by key; falls back to the
    alphabetically-first eligible candidate if none declares ``gsd``.
    ``None`` if there is no eligible candidate at all.
    """
    eligible = [
        (key, gsd)
        for key, media_type, roles, gsd in candidates
        if "data" in (roles or ()) and media_type not in _ARCHIVE_MEDIA_TYPES
    ]
    if not eligible:
        return None

    with_gsd = [(key, gsd) for key, gsd in eligible if gsd is not None]
    if with_gsd:
        return min(with_gsd, key=lambda kv: (kv[1], kv[0]))[0]
    return min(eligible, key=lambda kv: kv[0])[0]
