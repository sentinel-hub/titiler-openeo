# ADR 0002 — Band sources: deriving cube bands from non-raster STAC assets

- **Status:** Accepted
- **Date:** 2026-08-06
- **Deciders:** @emmanuelmathot
- **Supersedes / superseded by:** extends [ADR 0001](0001-sar-backscatter.md) §7.6
  and §7.10(b); supersedes the approach in PR #281
- **Issue:** [#348](https://github.com/sentinel-hub/titiler-openeo/issues/348)

---

## 1. Context

`sar_backscatter` (#342, #347) reaches Sentinel-1 calibration and noise annotation
XML through its own subsystem: an `AssetFetcher` protocol, an obstore S3/HTTP
client, and three module-global LRU caches. Issue #348 argues this is a second way
of getting data into a process, running alongside the one the codebase already has,
and that these inputs should instead be **bands on the cube**.

The issue also insists on the framing, which shapes the design: none of this data
is virtual. ESA computed the calibration LUTs, the GCPs are written into the TIFF,
the viewing angles are measured. The only thing missing is a path for the cube to
source a band from something that is not already a raster asset. "Band sources"
keeps that straight where "virtual bands" biases toward *computing* values.

### 1.1 Why not the two shapes already tried

**PR #281 ("virtual bands plugin mechanism")** introduced a `VirtualBandPlugin` ABC,
entry-point plugin discovery, per-collection JSON config
(`TITILER_OPENEO_VIRTUAL_BANDS_CONFIG`), Helm wiring and `cube:dimensions`
augmentation. It was sent back to draft. It needed all that machinery because it
bound plugins to collections by **hand-written configuration**, which is neither
self-describing nor reproducible: the same catalogue served from two deployments
could expose different bands.

**A downstream openEO process** was the next candidate, the direction recorded on
PR #281 at the time. It was rejected during this ADR's design for a decisive
structural reason: `load_collection` mosaics all items sharing a datetime into one
image, and calibration LUTs and GCP geometry are **per item**. A process runs after
that mosaic, so it cannot correctly serve a multi-item slice — which is exactly why
`sar_backscatter` rejects that case today (`sar.py:212`). Band sources placed in the
read path run *per item, before the mosaic*, and dissolve the problem.

### 1.2 Verified evidence

Checked live against the running catalogue APIs (2026-08-06). All three target
catalogues publish the needed discriminators at **collection** level, in
`item_assets`:

| `item_assets` entry | media type | roles | CDSE | ES | PC |
| --- | --- | --- | --- | --- | --- |
| `vv`/`vh`/`hh`/`hv` | `image/tiff; application=geotiff; profile=cloud-optimized` | `['data']` | yes | yes | yes |
| `schema-calibration-{pol}` | `application/xml` | `['metadata']` | yes | yes | yes |
| `schema-noise-{pol}` | `application/xml` | `['metadata']` | yes | yes | yes |
| `schema-product-{pol}` | `application/xml` | `['metadata']` | yes | yes | yes |
| manifest | `application/xml` | `['metadata']` | `safe_manifest` | `safe-manifest` | `safe-manifest` |
| `cube:dimensions` already declared | — | — | no | no | no |

Four consequences:

1. **The design is viable.** Media type and role are available before any item is
   fetched, so band discovery costs no extra request.
2. **Media type + role alone are insufficient.** Manifest, product, calibration and
   noise annotations are *all* `application/xml` + `['metadata']`. The asset **key**
   is the only discriminator, so it must be part of the match.
3. **The augmentation hook is live.** No target collection declares
   `cube:dimensions`, so `add_data_cubes_if_missing`'s gate (`stacapi.py:135`)
   passes today.
4. **A pre-existing defect to fix alongside.** CDSE's `Product` asset is
   `application/zip` with roles `['data','metadata','archive']`, so `getdimensions`
   (`stacapi.py:189`) advertises a zip archive as a spectral band today.
   `bands_name` is also an unordered `set` (`stacapi.py:187`), giving
   non-deterministic band order — issue #280.

> **Note on the item fixtures.** `tests/fixtures/sar/items/*.json` carry neither
> media types nor roles. That is an artifact of trimming, not a property of the
> catalogues. Do not draw conclusions about catalogue metadata from them.

### 1.3 What the existing SAR code already provides

The readers are thin, because the parsing and evaluation already exist and are
tested. `parse_calibration` (`annotation.py:173-178`) already reads **all four**
vectors into `Grid2D.values` — verified: `['betaNought', 'dn', 'gamma',
'sigmaNought']`. `CalibrationLUT.ellipsoid_incidence_angle` (`annotation.py:139`)
and `NoiseLUT.evaluate` exist. `geocode.get_gcps` and `build_inverse_map` produce
the source `(line, pixel)` coordinates. **No parser change is needed for the full
band surface.**

### 1.4 Two of #348's claims do not survive scrutiny

Recorded because the design depends on getting these right, not on the issue being
uniformly correct.

**"Laziness, caching and eviction come for free" — backwards.** The three caches
(`annotation._calibration_cache`, `annotation._noise_cache`,
`geocode._gcp_cache`) are module-global `LRUCache`s keyed on `hashkey(href)`,
holding *parsed* objects (~100 KB, not the 1–1.5 MB raw XML), with `condition=`
single-flight. They already span slices, tiles and requests. The cube — per-slice,
evicted per node — would be a worse home. #348's "re-fetch ~1.4 MB of XML per tile"
fear, which it calls the thing most likely to sink the refactor, does not
materialise: these caches keep working unchanged.

**"The fetch does not disappear, it moves" — correct, and it bounds the payoff.**
ADR 0001 §7.6's evidence is unrefuted: annotation XML is not a raster, GDAL VSI
byte-reads are not reachable from rasterio, and two of three catalogues serve the
XML over authenticated S3 only. `AssetFetcher` is therefore **retained**, demoted
from a top-level subsystem to a band reader's private dependency. What genuinely
goes away is `sar.py`'s asset-orchestration layer.

### 1.5 Memory does not block convergence

An earlier iteration of this design rejected letting `sar_backscatter` consume LUT
bands, on the grounds that it would retain every LUT band at once where `calibrate`
(`calibration.py:51-71`) currently frees `eta` and `a` each loop iteration. That
analysis assumed a *process-level* design with two separate passes over the stack.
At **reader** level the LUT bands are produced during a read that was happening
anyway, so retention is not doubled. Estimated at 1024²:

| | peak |
| --- | --- |
| fused today: 2 DN uint16 + inverse map 2×f8 held across the polarisation loop + f8 transients (`power`, `eta`, `a`) | ~54 MB |
| consuming bands: 6 bands f4 retained; inverse map transient per asset read | ~35–40 MB |

The inverse map (f8, held across the polarisation loop today) and the f8 transients
dominate the current path. **This is an estimate and is a gate, not an assumption**
— increment 2 measures it before increment 6 relies on it.

### 1.6 The graph carries what a planner needs

Parsed node data keys are
`['node_name', 'process_graph_uid', 'process_id', 'resolved_kwargs', 'result']`.
`resolved_kwargs` is a plain dict of the process arguments: a `sar_backscatter`
node holds `coefficient` and `noise_removal`; a `load_collection` node holds
`bands`. So **requirements can key on arguments**, not only on process name — an
extension of ADR 0001 §7.10(b), which specifies "a registry keyed by process name".

Edge direction matters: `out_edges` means "nodes I consume"
(`results_cache.py:53-59`), so a `load_collection` node's **downstream cone is
`nx.ancestors(graph.G, node)`** — verified to return exactly the `sar_backscatter`
and `save_result` nodes for a three-node graph.

---

## 2. Decision

**Band sources are discovered from STAC metadata and produced in the read path, and
`sar_backscatter` converges onto them via ADR 0001 §7.10(b)'s planner.**

Four parts:

1. **A registry** matching STAC-observable facts to a reader.
2. **Discovery** advertising the derived bands in `/collections` `cube:dimensions`.
3. **Production** in the read path, as a pseudo-asset with its own reader, per item,
   inside the mosaic.
4. **Convergence**: §7.10(b)'s reader-requirement planner injects the bands
   `sar_backscatter` needs, so its public API is unchanged and users never
   hand-request LUT bands.

### 2.1 The registry (in-code, shipped)

Entries live in a Python module. The asset-key regex's **named groups** do double
duty: they discriminate the asset *and* parameterize the band names and the sibling
lookup.

```python
@dataclass(frozen=True)
class BandSource:
    collection: "re.Pattern[str]"       # regex on collection id
    media_types: FrozenSet[str]         # {"application/xml"}
    roles: FrozenSet[str]               # {"metadata"}
    asset: "re.Pattern[str]"            # r"schema-calibration-(?P<pol>[a-z]{2})"
    bands: Tuple[Tuple[str, str], ...]  # ("{pol}_sigma0_lut", "sigma_nought"), ...
    sibling: Optional[str] = None       # "{pol}" -- measurement asset, for GCPs
    reader: Optional[Type[BaseReader]] = None
```

`bands` pairs a name template with a **quantity** string (increment 3):
opaque to the registry itself, threaded straight through to the reader's
`quantity` constructor kwarg, so one asset can back several
distinctly-computed bands without a reader subclass per quantity — one
calibration annotation's four LUT vectors plus the incidence angle all share
`CalibrationBandReader`, dispatching on `quantity` alone.

Shipped in code, not configured. The matcher is designed so a JSON/env override or
entry-point discovery is purely additive later, but neither ships now. **If
out-of-tree band sources ever become in-scope, the extension point should be the
process/reader registry itself** — generic entry-point discovery serving all
readers — never a band-source-specific plugin system. That is the lesson from #281.

### 2.2 Discovery (`stacapi.py`)

Run the registry over `collection.item_assets` in the bands block of
`getdimensions` (`stacapi.py:184-197`), union the derived names into
`dims["spectral"]`, and return a **sorted list** rather than a set. Filter the
existing `role == data` pass by media type so non-raster archives stop being
advertised (§1.2, consequence 4).

Place the augmentation so it also reaches collections that already declare
`cube:dimensions`: `add_data_cubes_if_missing` skips those entirely, while
`_fix_collection` (`stacapi.py:105`) runs unconditionally on the dict.
`get_collection` is `@cached` (`stacapi.py:218`) — a registry change must not serve
stale dimensions.

### 2.3 Production: reader-level pseudo-asset

`load_collection` calls `mosaic_reader(date_items, _reader, ...)` **per item**
(`stacapi.py:810`), so hooking at reader level means each item contributes its own
LUT band on its own footprint and the mosaic combines them exactly as it combines
DN. This is what ADR 0001 §7.10(b) meant by "calibrating per item *before* the
mosaic", and it is the decisive argument over a downstream process (§1.1).

Two live hooks carry it, with essentially no new plumbing:

- `_get_asset_info` (`reader.py:225`) — extend the valid-asset set with derived band
  names (`self.assets = self.input.get_assets().keys()`, `reader.py:212`; unknown
  names raise `InvalidAssetName` at `reader.py:237`) and inject the item plus the
  resolved sibling measurement href through `asset_info["reader_options"]`, which
  `MultiBaseReader.part` already forwards to the reader constructor.
- `_get_reader` (`reader.py:172`) — currently always returns `self.reader`; return
  the band-source reader for derived bands.

Derived bands then flow through `multi_arrays` like real assets, so requested band
ordering is handled for free. `part(bbox, width=…, height=…, dst_crs=…)` already
reaches individual asset readers, so a band reader has everything it needs to build
the inverse map and evaluate the LUT on the destination grid.

```text
load_collection(bands=["vv", "vv_sigma0_lut", "vv_noise_lut"])
  mosaic_reader(items, _reader, bbox)              # per ITEM
    SimpleSTACReader(item).part(bbox, assets=[...])
      multi_arrays:
        vv             -> OpenEOReader            (GeoTIFF, GCP-warped)
        vv_sigma0_lut  -> CalibrationBandReader   (XML + GCPs)
        vv_noise_lut   -> NoiseBandReader         (XML + GCPs)
    _reader post-step: harmonize derived-band masks
  -> mosaic combines per-item results
```

### 2.4 Three rules the read path must enforce

1. **Derived bands inherit the raster bands' combined validity mask, with no
   opt-out.** `multi_arrays` reads assets independently, and a LUT is honestly valid
   over the whole grid — but `ImageData._mask` is `logical_or.reduce(~array.mask)`,
   so a pixel counts as valid if **any** band is unmasked. An honestly-masked LUT
   band would therefore report a slice's nodata region as valid to `img.mask`,
   GeoTIFF nodata/alpha and `save_result`. `sar.py:355-367` documents this exact trap
   for the `mask` band. Force the inheritance in `_reader` (`reader.py:1011`),
   beside the existing `_apply_scale_offset` post-step (`reader.py:1049`). Values
   stay intact in `array.data`, so reading a band is still meaningful. Making the
   trap structurally unreachable is preferred over documenting a rule each band
   source must remember.
2. **Resolution estimation must map derived bands to their sibling.**
   `_get_assets_resolutions` (`reader.py:479`) does
   `if band_name not in item.assets: continue`, so derived names contribute no
   resolution and a derived-only request would silently fall back to 1024×1024.
3. **`_check_pixel_limit` must count derived bands.** `bands_count` counts only
   bands that yielded a resolution. With up to six derived bands per polarisation,
   under-counting would let a request allocate several times the intended memory.

### 2.5 Band surface

Six names per polarisation, each backed by existing tested code:

| band | source |
| --- | --- |
| `{pol}_sigma0_lut` | `grid.interp("sigmaNought", …)` |
| `{pol}_beta0_lut` | `grid.interp("betaNought", …)` |
| `{pol}_gamma0_lut` | `grid.interp("gamma", …)` |
| `{pol}_dn_lut` | `grid.interp("dn", …)` |
| `{pol}_ellipsoid_incidence_angle` | `CalibrationLUT.ellipsoid_incidence_angle` |
| `{pol}_noise_lut` | `NoiseLUT.evaluate` |

Polarisation prefixes are **mandatory**: #348's flat `sigma0_lut` is
under-specified for dual-pol, and two calibration assets in one item would collide.
The incidence angle is prefixed for the same reason even though it is physically
polarisation-independent. `sar_backscatter` keeps emitting its existing unprefixed
`ellipsoid_incidence_angle` output band. `schema-product-*` (geolocation grid) is
matched by no entry yet.

### 2.6 Convergence: §7.10(b)'s first client

ADR 0001 §7.10(b) designed a graph-driven reader-requirement mechanism and deferred
it, recording (L1063-1071) that it had **no first client** because no target
catalogue declares `raster:scale`/`raster:offset` on GRD measurement assets. Band
sources are that first client.

Faithful to §7.10(b)'s recorded decisions, mirroring `results_cache.py`:

1. **Requirement registry** keyed by process id, mirroring `_RECOMPUTE_PROCESSES`
   (`results_cache.py:50`). Because the needed LUT depends on the `coefficient`
   argument (`COEFFICIENT_LUT` maps `sigma0-ellipsoid -> sigmaNought`), entries are
   callables `(resolved_kwargs) -> Requirement`, not constants (§1.6). `Requirement`
   stays a small composable value object, not a SAR-shaped one, per §7.10(b)'s
   extensibility note.
2. **Pre-execution pass** over the parsed DAG. For each `load_collection` node,
   union the requirements of every process in `nx.ancestors(graph.G, node)`.
3. **Per-request process registry**: a shallow copy with `load_collection` rebound
   to add the required bands. §7.10(b) rejected `contextvar` (lost across
   `RasterStack`'s `ThreadPoolExecutor`), graph node-arg injection (mutates a
   user-visible artifact, validation risk, graph-hash churn) and backend instance
   state. Node-arg injection's one merit — the executed graph showing what the
   planner decided — is recovered by **logging resolved requirements per
   `load_collection` node**.

`sar_backscatter` then reads LUT bands already present in its input cube. Its
public API, spec and output band names do not change, and the injected bands are
consumed rather than leaking into its output.

---

## 3. Consequences

**Gained.** One code path for "non-raster asset → array on this grid", serving both
a user requesting `vv_sigma0_lut` and `sar_backscatter` internally. Multi-item
slices become calibratable, lifting `sar.py:212`. Composability: users can build
coefficients we did not anticipate. §7.10(b) stops being designed-but-unbuilt. Two
pre-existing defects (#280, the `Product` zip band) are fixed in passing.

**Accepted costs.** `AssetFetcher`, the annotation parsers and the three caches are
not deleted — they become band reader internals. The band names become a public
compatibility surface in `/collections`. The planner adds a pre-execution graph pass
to every request. `bandsources.registry.resolve_band`'s asset-key `fullmatch` has no
equivalent to the old `_find_annotation_asset`'s href-regex fallback or its "found
more than one, refuse to guess" ambiguity detection — a real narrowing of
catalogue-compatibility surface, accepted because all three target catalogues use the
exact `schema-{kind}-{pol}` key convention (§1.2, live-verified); a future catalogue
that doesn't would need a new registry entry, not a code change, but would not get a
graceful fallback either. Lifting the `sar.py:212` multi-item-per-slice rejection
(increment 6) is safe only because `stacapi.py`'s intra-datetime mosaic step hardcodes
`pixel_selection="first"` — mask-driven, not value-driven, so combined with derived
bands' forced mask inheritance (§2.4), DN and its calibration/noise bands are always
selected from the same winning item at every pixel. This is a real constraint on that
one call site, not a stylistic default: making it configurable to a value-driven method
(`Highest`/`Lowest`) would reopen the cross-item mixing risk this ADR's mosaic argument
(§1.1) depends on being closed.

**Deliberately not done.** Out-of-tree/plugin band sources; JSON or env registry
configuration; `schema-product-*` band sources; any change to `sar_backscatter`'s
signature, spec or output band names; fixing item-provenance loss in the 30+
`RasterStack.from_images` sites (only `map_tasks` and `filter_keys` preserve
`get_source_items`, which constrains process-side work, not read-path work).

### 3.1 Open risks

- **Resolved by increment 4 — registry rebinding survives `Process` wrapping, but
  only if the copy is isolated deeper than the ADR text implies.** `to_callable`
  looks up `process_registry[process_id].implementation` — `Process.__setitem__`
  re-applies `wrap_funcs` to whatever implementation it's given, so a rebound
  callable is wrapped exactly like any other registration
  (`test_isolated_copy_rebinds_without_leaking`). But `copy.copy(registry)` — the
  literal reading of "a shallow copy" — copies the `ProcessRegistry` object's
  attributes by reference, so `registry.store` (a `dict[namespace, dict[process_id,
  Process]]`) is **shared, not copied**. Rebinding `"load_collection"` on that
  "copy" mutates the dict the real, application-lifetime registry also reads from
  — every subsequent request, not just a concurrent one, would see the rebind
  (`test_naive_copy_copy_leaks_rebind_into_shared_registry`, confirmed against the
  real `openeo_pg_parser_networkx.process_registry.ProcessRegistry`). The correct
  per-request recipe copies `store` one namespace-dict level deeper —
  `{ns: dict(procs) for ns, procs in registry.store.items()}` — leaving untouched
  `Process` entries shared (they're never mutated in place) while isolating the
  one being rebound. Increment 5 must use this recipe, not `copy.copy` alone.
- **Resolved by increment 4 — §7.10(b)'s channel cannot resolve per graph-node
  identity, only per call-time signature.** `_map_node_to_callable`
  (`graph.py:339-382`) bakes each node's own `resolved_kwargs` into that node's
  `functools.partial` at graph-*construction* time, before rebinding is even
  relevant to it; the rebound `load_collection` implementation, once installed,
  receives only the kwargs a node happens to pass (`id`, `bands`, …), never a node
  identifier. Two `load_collection` nodes with identical `id`/`bands` are
  therefore provably indistinguishable at call time — proved directly against the
  real parser, not inferred (`test_identical_signature_nodes_are_indistinguishable_at_call_time`).
  "Dispatch on the incoming argument signature" is thus not one option among
  several — it is the *only* information the channel exposes. The other candidate
  named in the ADR, an unconditional single requirement applied to every call, is
  actively wrong, not just imprecise: `test_naive_unconditional_dispatch_contaminates_unrelated_collection`
  shows it injects a SAR-only band into an unrelated Sentinel-2 load that happens
  to share the callable. The decision: increment 5's planner keys resolved
  requirements by `(id, tuple(bands))` and unions across every `load_collection`
  node sharing that key (`test_signature_keyed_dispatch_serves_both_nodes`). This
  is a conservative union, not per-node precision — which sharpens the risk below
  from a possible edge case into the channel's structural default.
- **Sharpened by increment 4 — injected-band leakage to a signature-sharing
  sibling is not an edge case, it is what the chosen channel does by
  construction.** Two nodes with the same `id`/`bands` cannot be told apart (see
  above), so if one needs an injected band and the other doesn't, the union
  reaches both (`test_signature_keyed_dispatch_unions_same_signature_nodes`).
  `results_cache._tag_single_consumer` (`results_cache.py:82`) already computes
  single-consumer status from graph topology and is reusable. Increment 5 still
  decides whether to restrict injection to the single-consumer case, or strip
  injected bands post-mosaic for a signature-sharing consumer that didn't need
  them — increment 4 only establishes that some such mitigation is now required,
  not optional polish.

  Proof for all three points: `tests/test_reader_requirement_channel_spike.py`.
  Both fixes (the copy recipe and the signature-keyed dispatch) were confirmed
  load-bearing by reverting each in turn and observing the corresponding test
  fail before restoring the fix.
- **Inverse-map duplication across readers — resolved in increment 3.** Two
  derived bands of the same polarisation are two assets, so each reader would
  build its own TPS map for the same GCPs and grid unless something shares it.
  Not a module-global cache (a global LRU keyed by bbox/width/height/dst_crs in
  addition to href is a memory hazard — 2 × H×W float64 = 64 MB at 2048² — and
  has no natural eviction). Instead, `SimpleSTACReader` holds a plain dict plus
  a `threading.Lock` per item-read (`_inverse_map_cache`/`_inverse_map_lock`)
  and hands the *same* pair to every derived-band reader it constructs for that
  item's `part()` call; `BandReader._get_inverse_map` checks it first. Dies
  with the `SimpleSTACReader` instance — no eviction policy needed because
  nothing outlives one read.

  **The lock is load-bearing, not defensive.** `RasterStack` reads assets via a
  thread pool (`multi_arrays`/`create_tasks`), so several of one item's
  derived-band readers run concurrently. A first version used a bare
  `dict.get`-then-`dict.__setitem__` with no lock; requesting all five
  calibration bands together measured **2 builds, not 1** (`tests/
  test_calibration_band_reader.py::test_inverse_map_built_once_for_five_bands_from_one_asset`
  caught it directly, deterministically, across repeated runs) — two threads
  raced past the check before either stored a result, the same single-flight
  gap `annotation.py`/`geocode.py`'s own `condition=` caches exist to close.
  Fixed with a single coarse lock around the whole check-build-store section
  (per-key granularity buys nothing when the cache is effectively one entry).
- **Relocated, not resolved, by increment 6.** The literal loop this bullet named
  (`sar.py:313`, inline inverse-map construction) is gone — `sar_backscatter` builds
  no inverse map at all now, reading already-computed `A`/`eta` bands instead. But the
  underlying cost is still there, just moved: `bandsources/readers.py`'s
  `_inverse_map_cache` is keyed by `sibling_href` (increment 3), and VV/VH are
  *different* measurement assets with different hrefs, so their derived bands still
  build separate inverse maps even within one item's read — dual-pol still pays twice
  on identical geometry, if VV and VH do share GCP sets. That assumption remains
  unverified.
- **Derived-only requests** have no raster band to inherit a mask or a resolution
  from. Both are handled by §2.4; the semantics need documenting.

---

## 4. Implementation plan

Delivered as **stacked pull requests**, one per increment, based on this branch.

| # | Increment | Gate / abandon condition |
| --- | --- | --- |
| 1 | **Discovery only.** Registry module + `getdimensions` pass. Bands advertised; requesting one still fails at read time. Ships the `Product` fix and #280. | Abandon if derived names collide with real asset keys on any target catalogue. |
| 2 | **One reader end to end.** `NoiseBandReader` — the narrowest path exercising pseudo-asset resolution, `reader_options` injection, sibling GCP lookup, inverse map, grid alignment, mask inheritance and mosaicking. | **Gate:** per-tile wall time at 256² and 1024², decomposed (independent DN + `NoiseBandReader` reads) vs. fused (`sar_backscatter`'s existing DN-read-then-geocode-then-evaluate body) — not vs. DN alone, since both decomposed and fused pay the same TPS inverse-map cost once; that comparison would measure the cost of wanting a LUT at all, not the cost of decomposition. **Measured:** 1.00× at both 256² and 1024² on the real 440-GCP polar fixture (0.20 s / 3.03 s respectively for both shapes) — decomposition adds no measurable overhead. Abandon if mask inheritance cannot hold or the mosaic case does not work — both invalidate §2.3. |
| 3 | **The remaining bands.** `CalibrationBandReader` for the four LUT vectors plus the incidence angle — one class, all five, dispatching on a `quantity` string (`BandSource.bands` became `(name_template, quantity)` pairs; `ResolvedBand` carries `quantity` through to the reader's constructor). Not, as originally planned here, "the existing `bands`/`indexes` selection in `_get_options`" — that mechanism resolves a real STAC asset's own declared `eo:bands`/`bands` metadata, which a calibration XML asset has none of; `quantity` is this increment's own, simpler answer to the same "one asset, several bands" question. Also added `CalibrationLUT.dn()` (`sar/annotation.py`, mirroring the other three accessors — `dn` was already parsed, just not exposed) and the `_inverse_map_cache`/`_inverse_map_lock` memo the increment-2 risk log anticipated (see above). | **Gate:** a concurrency bug, not a timing one — see the risk log above. Fixed with a lock; `tests/test_calibration_band_reader.py`'s call-count tests are the regression guard, run in this project's default single-worker pytest config, not proof against every possible scheduling. |
| 4 | **Spike the requirement channel.** Settle both §3.1 unknowns. Deliverable is a decision plus a failing-then-passing test, not production code. | **Decided:** rebinding survives `Process` wrapping, but only with a copy recipe that isolates `ProcessRegistry.store` per namespace — `copy.copy(registry)` alone aliases it and permanently corrupts the shared, app-lifetime registry (confirmed against the real dependency, then fixed). The channel exposes no per-node identity, only each call's own `resolved_kwargs`, so increment 5 must key resolved requirements by `(id, tuple(bands))` and union across nodes sharing a signature — not attempt per-node precision, and not apply one requirement unconditionally (shown to contaminate an unrelated collection). This also promotes §3.1's "injected bands visible to other consumers" risk from a possible edge case to the channel's structural default. See `tests/test_reader_requirement_channel_spike.py`. |
| 5 | **Build the planner.** `titiler/openeo/reader_requirements.py`: requirement registry (`_REQUIREMENT_PROVIDERS`, keyed by process id, empty), the pre-execution DAG pass (`resolve_requirements`, signature-keyed per increment 4), the per-request registry (`build_per_request_registry`, using increment 4's verified copy recipe), resolved-requirement logging, wired into both executing `to_callable` call sites in `factory.py` (`openeo_result`, `openeo_xyz_service`). The three validate-only call sites are untouched — they never execute `load_collection`, so rebinding it would be inert there anyway. | **Gate met.** `_REQUIREMENT_PROVIDERS` ships empty — no process has converged onto the mechanism yet (increment 6 registers `sar_backscatter`) — so `resolve_requirements` returns `{}` for every graph today, and `build_per_request_registry` returns the *same* registry object, unchanged, whenever there's nothing to inject. That is the strongest form of "byte-identical read": not equivalent behavior through a wrapper, the same object. `tests/test_reader_requirements.py` locks this in alongside the mechanism itself (exercised via a synthetic requiring process, not `sar_backscatter`) and a `_signature_key` boundary test for the case increment 4 flagged — a UDP `from_parameter`'d `id`/`bands` is still unresolved in a node's static `resolved_kwargs` at graph-construction time, so such a node is left alone rather than guessed at. |
| 6 | **Convergence.** `sar.py`: deleted `_resolve_stack_assets`, `_resolve_polarisation_assets`, `_find_annotation_asset`, `_asset_href`, `_item_assets`, `_asset_fields` and the `sar.py:212` multi-item rejection (~150 lines). `sar_backscatter` now reads `A`/`eta` as ordinary bands (`{pol}_<suffix>_lut`/`{pol}_noise_lut`) via a `band_index` lookup, raising a clear `ProcessParameterInvalid` — not `KeyError` — if an expected band is missing, agnostic to whether the planner should have injected it or a caller was expected to request it explicitly. `calibration.py`'s `calibrate()` dropped `inverse`/`calibration`/`coefficient` entirely, down to `calibrate(dn, a=None, eta=None)` — literally arithmetic. Registered `sar_backscatter` in `reader_requirements._REQUIREMENT_PROVIDERS` via a new public `register_requirement_provider`, which needed the provider callable signature widened from `resolved_kwargs -> Requirement` to `(resolved_kwargs, load_collection_kwargs) -> Requirement` — `sar_backscatter`'s own arguments don't carry which polarisations were requested, only the ancestor `load_collection` node's do, the gap increment 5 flagged. A new `_COEFFICIENT_BAND_SUFFIX` table bridges `coefficient` to the band-name suffix — a *third* spelling alongside `COEFFICIENT_LUT`'s LUT-array name and `BandSource.bands`' `quantity` method name, kept in sync with `COEFFICIENT_LUT`'s keys by a dedicated test rather than derived mechanically (no clean transform between three genuinely different spellings). `tests/fixtures/sar/items/*.json` gained the `type`/`roles` fields §1.2 already verified live, closing a real gap: nothing previously exercised `resolve_band` against real per-catalogue item shapes, only synthetic ones. | **Gate confirmed, not abandoned** — increment 2 measured the decomposed read at 1.00× (no overhead). The load-bearing safety claim for lifting the multi-item rejection (`pixel_selection="first"` keeps DN and its LUT bands selected from the same winning item, never mixed — see §3 "Accepted costs") is proven end to end, not just asserted: `tests/test_sar_process.py::test_multi_item_mosaic_calibrates_per_item_without_cross_mixing` runs two items with distinct real fixture calibration/noise XML through the real `SimpleSTACReader`/`mosaic_reader`/`PixelSelectionMethod["first"]` path and checks each item's calibrated output against its own independently-computed oracle. A design-validation pass (a Plan-mode review before implementation, mirroring increment 4's rigor) caught a real crash bug — an unresolved UDP `from_parameter` coefficient reaching an unguarded `dict.get` inside the new requirement provider — and a silent fail-fast regression — the product-type check moving from eager (fails before any slice is read) to lazy (would only fail once a slice is consumed) — both fixed before landing, not left latent. |
| 7 | **Document.** `docs/src/sar-backscatter.md`: added the "Calibration and noise bands" table (the six `{pol}_<suffix>` bands, §2.5), a worked example requesting one directly, and a note that multi-item mosaic slices now calibrate correctly; fixed two stale rows in the error table (the multi-item rejection no longer exists; "asset does not resolve" became "band is missing from `data`"). ADR 0001 §7.10(b) and §7.6 each got a dated update paragraph rather than being rewritten in place — the original text stays as the historical record of what was designed and why, with a pointer to what actually shipped and where it deviated (the copy-isolation and signature-keyed-union refinements neither section anticipated). | — |

### 4.1 Verification

```bash
uv run pytest -q                       # full suite, expect no regressions
uv run pre-commit run --all-files       # isort, ruff, ruff-format, mypy
```

Per-increment, beyond the suite:

- **The oracle (increment 2).** A `{pol}_noise_lut` band must equal
  `annotation.get_noise(href).evaluate(line, pixel)` at the inverse-mapped
  coordinates `sar_backscatter` computes today, on the same grid. Increment 2 was
  deliberately chosen to have an exact reference; a band without one is a worse
  first step.
- **Existing numerics must not move.** `tests/test_sar_calibration.py`'s
  hand-computed `100/A²` values and
  `test_get_calibration_concurrent_access_fetches_once` (32 calls / 16 threads,
  `fetcher.calls == 1`) are the regression baseline for every increment.
- **`/collections` is right (increment 1).** `GET /collections/sentinel-1-grd` lists
  the derived bands, deterministically ordered, with no `Product` entry.
- **Convergence is invisible to users (increment 6).** The same graph —
  `load_collection(bands=["vv","vh"])` then
  `sar_backscatter(coefficient="sigma0-ellipsoid")` — produces the same output
  before and after, with no user-visible band-list change.
- **End to end.** `scripts/debug_graph.py` against a real S1 GRD item requesting
  `["vv","vv_sigma0_lut","vv_ellipsoid_incidence_angle"]`: incidence angle in tens
  of degrees with a smooth across-track gradient, and `vv_sigma0_lut` reproducing
  `sar_backscatter(coefficient="sigma0-ellipsoid")` when combined as `DN²/A²`.

---

## 5. Related

- [#348](https://github.com/sentinel-hub/titiler-openeo/issues/348) — this ADR's issue
- [#281](https://github.com/sentinel-hub/titiler-openeo/pull/281) — virtual bands
  plugin mechanism; prior art, superseded rather than merged
- [#280](https://github.com/sentinel-hub/titiler-openeo/issues/280) —
  non-deterministic band order, fixed by increment 1
- [#340](https://github.com/sentinel-hub/titiler-openeo/issues/340) — SAR
  backscatter Phase 1, which introduced the subsystem this ADR converges
- [ADR 0001](0001-sar-backscatter.md) §7.6 (asset fetching), §7.10(b) (reader
  capability negotiation)
