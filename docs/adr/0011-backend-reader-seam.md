# ADR 0011 — One reader seam for backends

- **Status:** Proposed
- **Date:** 2026-10-15
- **Deciders:** @emmanuelmathot
- **Related:** [#379](https://github.com/sentinel-hub/titiler-openeo/issues/379), [#378](https://github.com/sentinel-hub/titiler-openeo/pull/378), [#427](https://github.com/sentinel-hub/titiler-openeo/pull/427), [ADR 0002](0002-band-sources.md) §2.4, [ADR 0005](0005-asset-href-signing.md) §2.2, [ADR 0007](0007-multiband-assets.md)

---

## 1. Context

A backend built on titiler-openeo can need a different per-asset reader. The
known case is titiler-eopf: its items carry Zarr assets, so it subclasses
`SimpleSTACReader` and routes Zarr media types to a `GeoZarrReader` in
`_get_reader`. That subclass is small and correct. The problem was how to
make the core use it.

Before this ADR, `SimpleSTACReader` was hardcoded at three sites in
`titiler/openeo/reader.py`: `_get_target_crs_bbox`, `_get_cube_resolutions`
and `_reader`. `LoadCollection.load_collection` hardcoded `_reader` and the
way `bands` became `assets`. The only way to change one of these was to copy
the enclosing function. titiler-eopf copied `_reader` (57 lines to change one)
and the whole `load_collection` (about 250 lines to change three things: the
asset parser, the reader and an `EmptyMosaicError` conversion).

A copy is a snapshot. An audit on 2026-08-14 found that the two copies were
missing, with no intent:

| Added upstream | Missing from the copy | Effect |
| --- | --- | --- |
| `_inherit_derived_band_masks` (ADR 0002 §2.4) | `_reader` | derived bands made no-data regions read as valid |
| item id and datetime in the retry log | `_reader` | warnings said only "RasterioIOError encountered" |
| scale/offset sources (#427, ADR 0009) | `_reader` | raw DN returned instead of reflectance |
| `max_items` guard (#302) | `load_collection` | wide temporal extents silently truncated |
| `"items"` task metadata for `sar_backscatter` | `load_collection` | process could not reach its source items |

Each later upstream change (for example the `signer` parameter of #371, then
its revert in #382) had to be re-synced by hand.

## 2. Decision

### 2.1 `reader_cls` is the single seam

Every function that builds a `SimpleSTACReader` takes the class as a
parameter named `reader_cls`:

- `_reader(item, bbox, *, reader_cls=None, **kwargs)`
- `_estimate_output_dimensions(..., reader_cls=None)`
- `LoadCollection.reader_cls` and `LoadStac.reader_cls` (attrs fields)

`LoadCollection` and `LoadStac` forward their field to every read and every
estimate they make. `LoadStac` also forwards it to the `LoadCollection` it
builds for a Collection or Catalog. `reader_cls` reaches `_reader` as an
ordinary `mosaic_reader`/`create_tasks` keyword. It is keyword-only in
`_reader` and consumed there, so `part()` never sees it.

A backend wires it one time, at deployment:

```python
loaders = LoadCollection(stac_client, signer_key=signer_key, reader_cls=STACReader)
```

The seam is a class, not a read function, for two reasons. The dimension
estimate must open each item with the same class the read will use, so that
bounds, CRS and inner bands (ADR 0007) match. And one concept is enough: a
backend that needs to change what happens around the read (retries, masks,
scale/offset) must change it for every backend, in `_reader`, not in a copy.

### 2.2 `_bands_to_assets` is the band-notation hook

`LoadCollection._bands_to_assets(bands)` returns what `mosaic_reader` reads
as `assets`. The default is the identity: a band name is an asset name, or a
derived or inner band that `SimpleSTACReader` resolves. A backend whose band
notation carries reader options (titiler-eopf's `asset|bands=b04`) overrides
it. Only the read uses the result. `bands` itself still labels the output and
drives the resolution estimate.

This is a method, not a field, because the backends that need it already
subclass `LoadCollection`, and a method has no default-binding question.

The post-read steps in `_reader` (mask inheritance, scale/offset) key on
asset *names*, so a rio-tiler asset dict (`{"name": ..., "bands": [...]}`)
works there too (`_asset_names`). Known limit: one dict that reads several
bands gives one name for several bands, so `_apply_scale_offset` skips that
read with its usual count-mismatch warning. A backend whose multi-band assets
carry their own scaling (Zarr) is not affected; one that needs STAC
scale/offset on such a read should request one band per asset entry.

### 2.3 Defaults are `None`, resolved at call time

`reader_cls` defaults to `None` everywhere and `_default_reader_cls` resolves
it to the module's `SimpleSTACReader` when called. Several test modules
monkeypatch `titiler.openeo.reader.SimpleSTACReader`; a def-time default
(`reader_cls=SimpleSTACReader`) would freeze the real class and break them.

### 2.4 An empty date group is skipped, not fatal

In `load_collection`, each date group's task converts `EmptyMosaicError` to
`TileOutsideBounds`. `RasterStack` already skips `TileOutsideBounds` (its
default allowed exception) with a warning. One date with no pixels in the
bbox now drops out of the stack instead of failing the request. titiler-eopf
had done this in its copy; the behaviour is right for every backend.

### 2.5 The estimate opens each item one time

`_estimate_output_dimensions` builds one reader per item in an `ExitStack`
and hands the list to `_get_target_crs_bbox` and `_get_cube_resolutions`.
Before, each helper built its own reader, so `__attrs_post_init__` (band
resolution and, for SAR items, a header open, ADR 0001 §1.6) ran two times
per item. The readers hold no file handles, so keeping them open costs only
memory. `Dims` also carries `output_bbox`, which `load_collection` used to
recompute.

## 3. Consequences

- titiler-eopf deletes its `_reader` copy, its `load_collection` override and
  the helpers that served it (`_make_mosaic_task`, `_build_tasks`,
  `_group_items_by_date`). It keeps `STACReader` and adds a
  `_bands_to_assets` override that calls its `_parse_asset`. Every later
  change to `_reader` or `load_collection` reaches it on upgrade.
- Provider and format specifics stay in the backend. The core gains no
  knowledge of Zarr, of `|` notation, or of any catalogue
  ([deployment-scoped configuration](0009-scale-offset-sources.md), #377).
- `_get_target_crs_bbox` and `_get_cube_resolutions` take opened readers
  instead of items. They are private; the two tests that called them directly
  were updated.
- Behaviour change: an all-empty date group is skipped with a warning (§2.4).
  A request where every date is empty still fails, at `save_result`, with an
  empty stack.

## 4. Non-goals

- `OpenEOReader` (the per-asset raster reader) is unchanged. It stays the
  place for per-asset read fixes (ADR 0001 §7.10).
- `load_url` keeps the default reader. It builds its own single-asset item
  and has no backend behind it.
- No `Protocol` for the reader. `Type[SimpleSTACReader]` is the contract: a
  backend subclasses it and overrides `_get_reader`, `_get_options` or
  `part`.
