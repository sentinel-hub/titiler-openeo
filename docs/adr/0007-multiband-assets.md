# ADR 0007 — Bands published inside one STAC asset

- **Status:** Accepted
- **Date:** 2026-08-14
- **Deciders:** @emmanuelmathot
- **Related:** issue #379

---

## 1. Context

Every catalogue this backend read until now gave each band its own STAC
asset: `B02`, `B03`, ... one asset key per band, so an openEO `bands` name is
always an asset key. `_get_assets_resolutions`
(`titiler/openeo/reader.py`) reads `item.assets` directly on that assumption
— `if band_name not in item.assets: ...` falls through to the band-source
registry (ADR 0002) and nowhere else.

EOPF's Copernicus Sentinel-2 L2A catalogue does not fit that shape. A single
`reflectance` asset (Zarr) holds twelve bands:

```json
"reflectance": {
  "type": "application/vnd.zarr; version=3; profile=multiscales",
  "gsd": 10,
  "roles": ["data", "reflectance"],
  "bands": [
    {"name": "b01", "gsd": 20, "eo:common_name": "coastal", "eo:center_wavelength": 0.443},
    {"name": "b02", "gsd": 10, "eo:common_name": "blue",    "eo:center_wavelength": 0.49}
  ]
}
```

Before this ADR, requesting `bands=["blue"]` against this item failed:
`getdimensions` never advertised `blue` (only `reflectance`, the asset key),
`_add_band_summaries` skipped the asset outright once it saw more than one
`eo:bands` entry, and `_get_asset_info` raised `InvalidAssetName` for any name
that was not a literal asset key.

### 1.1 The read half already existed

`SimpleSTACReader._get_options` already resolves an `AssetWithOptions["bands"]`
list to `method_options["indexes"]`, matching against the asset's own
`bands`/`eo:bands` metadata by `eo:common_name` first, then `common_name`,
then `name`. `_get_asset_info` already accepts the dict form
`{"name": "reflectance", "bands": ["blue"]}`. Nothing in three call sites
(discovery, read, resolution estimation) ever constructed that dict from a
bare band name — the mechanism existed, unreached.

### 1.2 The trap: bands ≠ independently-addressable bands

A true-colour `TCI` asset also carries several `eo:bands` entries —
`[B04, B03, B02]` — describing the fixed RGB channels of *one* rendered
image. Bare band count on its own cannot distinguish that from EOPF's
`reflectance`, where each band is an independent variable a caller may
request alone. Treating any asset with 2+ `bands` entries as expandable was
tried first and broke exactly this case (`tests/test_band_summaries.py`
already asserted a `TCI`-style asset must not expand).

The distinguishing signal is not "how many bands" but "what kind of asset":
a rendering/preview product versus a data product. STAC's own asset
`roles` say exactly that — a TCI-style composite is conventionally tagged
`visual` (sometimes `overview`/`thumbnail`); a genuinely multi-band data
asset like `reflectance` is not. This codebase already draws the same line
elsewhere (`getdimensions`'s `"data" in roles` check), and it does not
depend on a catalogue touching the datacube extension — an earlier version
of this ADR gated on `cube:variables` instead, which was rejected on review
as unreliable: plenty of catalogues describe genuinely independent bands
without ever declaring it, and requiring it would silently fall back to "one
entry per asset" for them.

## 2. Decision

One resolver, `titiler/openeo/assetbands.py`, mirrors
`bandsources`' `derive_bands`/`resolve_band` shape — the same shape already
solving "a requested name is not a literal asset key" for band-source bands,
already consumed by both discovery (`stacapi.py`) and read (`reader.py`).
Everything here is derived from the item's own metadata; there is no
registry, matching ADR 0002 §2.1's "no hand-written per-collection
configuration" rule.

```python
def asset_band_facts(assets) -> List[Tuple[asset_key, bands]]: ...
def resolve_asset_bands(facts) -> Dict[display_name, ResolvedAssetBand]: ...
```

**Expansion rule.** An asset expands only when it declares **two or more**
bands and carries **no rendering role** (`visual`/`overview`/`thumbnail`).
Everything else — no `bands` at all, exactly one band, a rendering-role
asset regardless of band count — keeps its asset key untouched:

- A single-band asset keeps its key even when the band's own `name` differs
  from it (CDSE's `B02_10m` holds a band named `B02`; earth-search's `blue`
  holds `B02` too) — expanding would rename bands existing process graphs and
  services already reference, and collapse CDSE's per-resolution keys.
- A composite carrying a rendering role keeps its key (§1.2).
- Publishing a `bands` array at all, on an asset that is neither of the
  above, is itself read as the catalogue's declaration that these bands are
  worth describing individually — there is no further extension required on
  top of that.

**Naming precedence matches `_get_options` exactly**: `eo:common_name`, then
`common_name`, then `name`. A resolved display name that `_get_options`
cannot itself look back up would resolve at discovery time and fail at read
time — the two must use one precedence, not two that happen to agree today.

**Name collisions are qualified, not silently won.** If two multi-band
assets on one item both publish `blue`, both become
`{asset_key}_blue`; a unique name stays bare.

### 2.1 One resolver, four call sites, no `LoadCollection` hook

The issue that prompted this (#379) suggested an `asset_parser` callable on
`LoadCollection`. That would not have reached `_get_assets_resolutions`,
which needs the same mapping and operates below `LoadCollection` entirely,
nor `getdimensions`, which runs at the *collection* level before any item
exists. A backend-supplied parser and this module's own derivation would
also have had to agree by construction, which is exactly the discipline a
shared resolver enforces instead of trusting two independent
implementations to.

| call site | change |
| --- | --- |
| `stacapi.py::getdimensions` | expand a multi-band asset into its band names in `cube:dimensions.spectral`, instead of adding the asset key |
| `stacapi.py::_add_band_summaries` | one `summaries.bands` entry per band, each with its own `eo:common_name`/wavelength/`gsd` — previously skipped for any asset with >1 `eo:bands` entry |
| `reader.py::SimpleSTACReader._get_asset_info` | a name that is not a real asset key is checked against the item's own `_inner_bands` (precomputed once in `__attrs_post_init__`, same pattern as `_derived_bands`) *after* derived bands, *before* raising `InvalidAssetName` |
| `reader.py::_get_assets_resolutions` | resolves through `src_dst._inner_bands` first, using the band's **own** declared `gsd` rather than the owning asset's (`reflectance` declares `gsd: 10`; `b01` is native 20m) |

`load_collection`/`_process_spatial_extent` need **no change**: they already
pass the flat requested-`bands` list straight through as `assets=bands`, and
`_get_asset_info` now resolves each bare name transparently. This also keeps
`_inherit_derived_band_masks`' invariant intact — one requested name still
produces exactly one output band, so `kwargs["assets"]` stays index-aligned
with `img.array`. Grouping several inner bands into one `{"bands": [...]}`
open (avoiding re-opening the same asset once per band) is a real
optimisation left for later: it would need `_inherit_derived_band_masks` to
expand a group back into per-band indices, and is not needed for
correctness.

### 2.2 Precedence: derived bands, then inner bands, then asset keys

`_get_asset_info` checks `_derived_bands` (ADR 0002) before `_inner_bands`.
A band-source rule has already matched a specific annotation asset by the
time it registers a name; an inner-band match on the same name must not
shadow it. The two are not expected to collide on any real item — Sentinel-1
GRD collections do not also carry EOPF-shaped reflectance assets — but the
order is a deliberate, tested guarantee
(`test_derived_bands_still_win_over_inner_bands_on_a_name_collision`), not an
accident of insertion order.

### 2.3 A pre-existing gap this surfaced: assets with only `gsd`

`_get_asset_resolution` (unrelated to this ADR's own mechanism) had three
fallbacks — `proj:transform`, `proj:shape` + item bbox, the reader's own
transform — none of which consult an asset's declared `gsd`. EOPF's
`AOT_10m`/`SCL_20m`/`WVP_10m` carry only `gsd` and none of those three, so
before this ADR they silently contributed **no** resolution at all,
regardless of multi-band assets. A fourth fallback — the asset's own `gsd` —
closes this. It is a one-line, backward-compatible addition (every existing
asset that reached this fallback previously had none of the first three
either, and previously contributed nothing): without it, "supports EOPF"
would have been only half true, since the same item's single-band assets
would still fail to size.

## 3. Consequences

**Gained.** `load_collection`, discovery and resolution estimation now agree
on every band name for a multi-band-asset catalogue, and titiler-eopf's need
to fork `load_collection` for band addressing (#379) is answered without any
new extension point. Existing catalogues (CDSE, earth-search, Planetary
Computer) are provably unaffected — `resolve_asset_bands` returns `{}` for
all three (verified against their fixtures).

**Accepted costs.** One more precomputed dict on `SimpleSTACReader`
(`_inner_bands`, same lifetime and cost class as `_derived_bands`). A second
place — beside `_get_options` — that must agree on band-name precedence,
documented and tested rather than merely hoped-for.

**Deliberately not done.** Grouping several requested inner bands into one
asset open (§2.1's noted follow-up). A `LoadCollection`-level parser hook —
the resolver already reaches every call site that needs it, so a hook would
only add a second answer that has to agree with the first. Any change to
`_get_options`'s existing precedence itself.

## 4. Amendment (2026-09-04, issue #397)

`resolve_asset_bands` originally registered only the precedence-winning
display name (§2's "naming precedence" rule). When a band carried both a
display name and its own, different STAC `name` (EOPF's
`{"name": "b04", "eo:common_name": "red"}`), only `"red"` was resolvable —
`load_collection(bands=["b04"])` raised `InvalidAssetName` even though `b04`
is the band's own declared identifier in the same catalogue metadata.

`resolve_asset_bands` now registers **both** aliases when they differ, both
mapping to the same `ResolvedAssetBand` — whose `.band_name` stays the
precedence winner regardless of which alias reached it, so `_get_options`
(§1.1) can still resolve it. Collision qualification (§2's "name
collisions are qualified") now applies per alias rather than per band: an
asset's raw `name` colliding with another multi-band asset's own alias is
qualified independently of whether its display name also collides.

No call site other than `assetbands.py` itself changed. Discovery
(`getdimensions`, `_add_band_summaries`) now advertises both aliases too, as
a direct consequence of sharing one resolver (§2.1) rather than a separate
decision — the same "a band the read path accepts should be advertised, and
vice versa" property `test_every_advertised_band_is_actually_readable`
already enforced continues to hold for both names.

## 5. Related

- [ADR 0002 — Band sources](0002-band-sources.md) — the registry shape this
  reuses, and the precedence rule in §2.2.
- [ADR 0004 — Sentinel-2 view/sun angle bands](0004-sentinel2-view-sun-angle-bands.md)
  — the other backend that resolves a requested name against something other
  than a literal asset key.
- `titiler/openeo/assetbands.py`, `tests/test_assetbands.py`,
  `tests/test_multiband_assets.py`.
