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

Increment 1 (issue #348) covers discovery only: this module says which band
names a collection's assets make available, for `/collections`
`cube:dimensions`. Actually producing them at read time is a later increment.
"""

import re
from dataclasses import dataclass
from typing import FrozenSet, Iterable, List, Optional, Sequence, Set, Tuple

__all__ = ["BandSource", "derive_bands"]


@dataclass(frozen=True)
class BandSource:
    """One collection-level rule: which assets give rise to which band names.

    ``collection`` and ``asset`` are pre-compiled patterns so a registry with
    many entries does not recompile them per lookup. ``collection`` is matched
    with :meth:`re.Pattern.search` (a substring match, since deployments may
    suffix or version a collection id); ``asset`` is matched with
    :meth:`re.Pattern.fullmatch` against the whole asset key, since asset keys
    are exact tokens, not free text.

    ``bands`` are name templates formatted with the ``asset`` match's named
    groups (``str.format(**match.groupdict())``), so ``"{pol}_noise_lut"``
    resolves to ``"vv_noise_lut"`` when ``asset`` matched with ``pol="vv"``.
    """

    collection: "re.Pattern[str]"
    media_types: FrozenSet[str]
    roles: FrozenSet[str]
    asset: "re.Pattern[str]"
    bands: Tuple[str, ...]


def derive_bands(
    collection_id: str,
    assets: Iterable[Tuple[str, Optional[str], Sequence[str]]],
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
    """
    assets = list(assets)
    names: Set[str] = set()

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
            names.update(band.format(**groups) for band in source.bands)

    return sorted(names)
