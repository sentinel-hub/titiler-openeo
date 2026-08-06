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
to every request.

**Deliberately not done.** Out-of-tree/plugin band sources; JSON or env registry
configuration; `schema-product-*` band sources; any change to `sar_backscatter`'s
signature, spec or output band names; fixing item-provenance loss in the 30+
`RasterStack.from_images` sites (only `map_tasks` and `filter_keys` preserve
`get_source_items`, which constrains process-side work, not read-path work).

### 3.1 Open risks

- **Injected bands are visible to a `load_collection` node's other consumers.** If
  one node feeds both `sar_backscatter` and, say, `reduce_dimension`, the extra bands
  change the second consumer's result. `results_cache._tag_single_consumer`
  (`results_cache.py:82`) already computes single-consumer status from graph
  topology and is reusable. Increment 4 decides whether to restrict injection to the
  single-consumer case initially, or to strip injected bands for non-requiring
  consumers.
- **§7.10(b)'s channel does not obviously support per-node requirements.** It
  rebinds **one shared** `load_collection` callable, yet asserts that multiple
  `load_collection` nodes "are resolved independently". Those do not reconcile as
  written. Candidate resolutions: dispatch on the incoming argument signature
  (`resolved_kwargs` carries `id` and `bands`), or take a conservative union across
  nodes. Settled by the increment-4 spike.
- **Does registry rebinding survive `openeo_pg_parser_networkx`'s `Process`
  wrapping?** §7.10(b) flags this as worth a spike; increment 4 does it.
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
- **`sar.py:313` builds the inverse map inside the per-polarisation loop**, so
  dual-pol pays for it twice on identical geometry today. Increment 6 removes the
  loop. Whether VV and VH of one product actually share GCP sets is an unverified
  assumption.
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
| 4 | **Spike the requirement channel.** Settle both §3.1 unknowns. Deliverable is a decision plus a failing-then-passing test, not production code. | — |
| 5 | **Build the planner.** Requirement registry, DAG pass, per-request registry, resolved-requirement logging. | A graph with no requiring process must produce a byte-identical read. |
| 6 | **Convergence.** `sar_backscatter` consumes injected bands; `calibrate` reduces to arithmetic; delete `_resolve_stack_assets`, `_resolve_polarisation_assets`, `_find_annotation_asset` and the `sar.py:212` rejection (~150 lines). | Abandon if increment 2's gate showed the decomposed read is materially more expensive. Then stop at 3, keep `sar_backscatter` fused, and record why here. |
| 7 | **Document.** `docs/src/sar-backscatter.md` band table; update ADR 0001 §7.10(b)'s "deferred, no first client" caveat and §7.6's framing of `AssetFetcher`. | — |

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
