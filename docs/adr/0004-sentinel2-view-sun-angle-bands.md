# ADR 0004 — Sentinel-2 view/sun angle bands via band sources

- **Status:** Accepted
- **Date:** 2026-08-07
- **Deciders:** @emmanuelmathot
- **Supersedes / superseded by:** extends [ADR 0002](0002-band-sources.md);
  supersedes the approach in PR #281
- **Closes:** PR #281

---

## 1. Context

PR #281 ("virtual bands plugin mechanism") answered a real bug: a user
running the SNAP LAI notebook against the CDSE-deployed titiler-openeo hit
`ValueError: Invalid band name/index 'viewZenithMean'`. Sentinel-2 view/sun
angle bands used by SentinelHub-style eval scripts (`viewZenithMean`,
`viewAzimuthMean`, `sunZenithAngles`, `sunAzimuthAngles`) do not exist as
raster assets in any target STAC catalogue — the openEO client validates
requested bands against `cube:dimensions.spectral.values`, derived purely
from `item_assets` with a `data` role, so these are rejected outright.

### 1.1 Why not PR #281's mechanism

Covered in full by [ADR 0002 §1.1](0002-band-sources.md#11-why-not-the-two-shapes-already-tried):
a `VirtualBandPlugin` ABC bound to collections by hand-written JSON
config, rejected as neither self-describing nor reproducible. ADR 0002
built the replacement — the band-sources registry — but its only shipped
client is Sentinel-1 calibration/noise bands. This ADR is that mechanism's
second client, and the one PR #281 was actually for.

### 1.2 Why not the flattened STAC item properties alone

CDSE and Earth Search's `sentinel-2-l2a` items expose `view:incidence_angle`,
`view:azimuth`, `view:sun_azimuth`, `view:sun_elevation` as scalar item
properties (STAC `view` extension). An early pass of this design was built
directly on those properties, broadcasting each as a constant band. That
approach was abandoned for two reasons, once checked against the real data:

1. **Planetary Computer's `sentinel-2-l2a` items carry none of the `view:*`
   properties at all** — only `s2:mean_solar_zenith`/`s2:mean_solar_azimuth`
   (sun angles only, no view angles, verified live). Item properties alone
   would make `viewZenithMean`/`viewAzimuthMean` catalogue-dependent by
   construction, needing per-catalogue fallback special-casing.
2. **The properties are coarser than the source data they summarize.**
   Every Sentinel-2 product ships a granule-level tile metadata file,
   `GRANULE/<granule>/MTD_TL.xml`, published as a STAC asset
   (`granule_metadata` on CDSE/Earth Search, `granule-metadata` — hyphen —
   on Planetary Computer; `application/xml`, role `metadata`), which
   contains the *actual measured* geometry: a 23×23, 5000 m-step spatial
   grid of sun zenith/azimuth angles, and, separately, one viewing
   zenith/azimuth pair per spectral band. The flattened `view:*` properties
   are themselves computed from this file (§1.3 proves it). Reading the
   real asset instead of its summary is both more accurate (a genuine
   per-pixel sun-angle field instead of one tile-wide scalar) and, since all
   three catalogues publish the same ESA-produced XML unchanged, uniform
   across catalogues — eliminating the Planetary Computer gap in (1)
   entirely, except for actually fetching the asset (§1.3, §3.1).

This mirrors ADR 0002's own framing: "none of this data is virtual... the
viewing angles are measured." Reading the measurement itself, not a
catalogue's flattened summary of it, is the more direct application of that
principle.

### 1.3 Verified evidence (2026-08-07)

Checked live against all three target catalogues' real STAC APIs and, for
Earth Search (public HTTPS, no auth required), by downloading and parsing
`granule_metadata.xml` directly with `xml.etree.ElementTree`.

**Asset presence and naming:**

| Catalogue | asset key | href scheme | anonymous fetch |
| --- | --- | --- | --- |
| CDSE | `granule_metadata` | `s3://eodata/...MTD_TL.xml` (+ `alternate.https`, `auth:refs: [oidc]`) | needs credentials — same shape as SAR's own annotation XML, already handled by `ObstoreFetcher` |
| Earth Search | `granule_metadata` | `https://sentinel-cogs.s3.us-west-2.amazonaws.com/...granule_metadata.xml` | **yes** — plain public HTTPS, confirmed by direct `curl` |
| Planetary Computer | `granule-metadata` (hyphen) | `https://sentinel2l2a01.blob.core.windows.net/...MTD_TL.xml` | **no** — `HTTP 409 PublicAccessNotPermitted`, confirmed by direct `curl` |

**XML structure** (parsed from a real Earth Search item,
`S2B_29VPG_20260807_0_L2A`):

- `Tile_Geocoding/HORIZONTAL_CS_CODE` (e.g. `EPSG:32629`) +
  `Tile_Geocoding/Geoposition[resolution=10]/{ULX,ULY,XDIM,YDIM}` — ordinary
  affine georeferencing. **Sentinel-2 imagery is not GCP-referenced** —
  unlike SAR, no ground-control-point inverse mapping is needed, only a CRS
  reprojection of destination pixel centers into the tile's own CRS.
- `Tile_Angles/Sun_Angles_Grid/{Zenith,Azimuth}`: two real 23×23 grids,
  5000 m step (`COL_STEP`/`ROW_STEP`), each a `Values_List` of
  space-separated floats — a genuine spatial field.
- `Tile_Angles/Mean_Viewing_Incidence_Angle_List`: one
  `Mean_Viewing_Incidence_Angle[bandId=0..12]` entry per Sentinel-2 band (13
  entries confirmed), each its own `ZENITH_ANGLE`/`AZIMUTH_ANGLE`. **No
  pre-averaged value exists in the file** — "Mean" in `viewZenithMean`/
  `viewAzimuthMean` means mean-across-bands, computed by this ADR's reader.
- `Tile_Angles/Viewing_Incidence_Angles_Grids` (per-band, per-detector
  spatial grids) also exists but is **not used** — `viewZenithMean`/
  `viewAzimuthMean` are semantically scalar-per-scene, matching their name,
  so `Mean_Viewing_Incidence_Angle_List` is the correct source, not a
  higher-fidelity spatial one.

**A bit-identical oracle.** For the same item, Earth Search's flattened
`view:incidence_angle` (`8.396813497980254`) equals the arithmetic mean of
the 13 `Mean_Viewing_Incidence_Angle/ZENITH_ANGLE` values to full float
precision, and `Mean_Sun_Angle`'s `ZENITH_ANGLE`/`AZIMUTH_ANGLE` exactly
match `90 − view:sun_elevation`/`view:sun_azimuth`. This proves Earth
Search computes its flattened properties by exactly this algorithm, and
gives a real, live-sourced numeric oracle for tests. A proper **circular
mean** of the same 13 azimuth values (`102.9626524696764`) differs from the
naive arithmetic mean (`102.96270254490969`) by ~1e-4° here — negligible in
this example, but a naive arithmetic mean is wrong in general near the
0°/360° wrap boundary, so `viewAzimuthMean` uses a circular mean
(`atan2` of mean sin/cos), not an arithmetic one.

**Planetary Computer's access gap is not new to this feature.**
[ADR 0001 §7.6](0001-sar-backscatter.md#76-fetching-the-annotation-assets)
already recorded, for SAR's annotation XML, that Planetary Computer needs a
SAS token even for its own `application/xml` metadata assets. This ADR hits
the same class of problem for a different asset; see §3.1.

---

## 2. Decision

**`granule_metadata`/`granule-metadata` is a real STAC asset, so this reuses
ADR 0002's `BandSource`/`BandReader` registry directly** — two new registry
entries and one small, additive extension to `registry.py` — rather than a
new mechanism.

### 2.1 Registry extension: dynamic sibling selection

`BandSource.sibling` (ADR 0002 §2.1) is a fixed string template formatted
from the matched asset's own regex groups — correct for S1, where the
calibration asset key itself carries the polarisation that names its
sibling (`"{pol}"`). `granule_metadata`'s key carries no such group, and the
real raster "sibling" needed for resolution/mask-inheritance purposes (ADR
0002 §2.4 rules 2–3) has a different key per catalogue for the identical
collection id, verified live:

| Catalogue | red-band-equivalent asset key | `gsd` |
| --- | --- | --- |
| CDSE | `B04_10m` | `10` |
| Earth Search | `red` | `10` |
| Planetary Computer | `B04` | `10.0` |

No fixed template expresses this, so `BandSource.sibling` widens to also
accept a callable — not a second field, since no source ever needs both a
regex group *and* the item's other assets to pick its sibling:

```python
SiblingCandidateFacts = Tuple[str, Optional[str], Sequence[str], Optional[float]]

@dataclass(frozen=True)
class BandSource:
    ...
    sibling: Optional[
        Union[str, Callable[[Sequence[SiblingCandidateFacts]], Optional[str]]]
    ] = None
    reader: Optional[Type[BaseReader]] = None
```

`resolve_band` dispatches on type: a `str` is formatted with the matched
asset's regex groups exactly as before; a callable is called with
`sibling_candidates` instead. `pick_nominal_sibling_by_resolution` (the
callable Sentinel-2's two entries use) picks the smallest declared `gsd`
among real, non-archive, `role=data` candidates, tie-broken alphabetically;
falls back to the alphabetically-first eligible candidate if none declares
`gsd`. `sibling_candidates` is an optional parameter on `resolve_band`,
defaulting to `None` — every existing S1 entry's `sibling` stays a plain
string, so every existing call site is unaffected, proven by a test
asserting identical `resolve_band` output for an S1-shaped source called
with and without the new parameter.

### 2.2 Discovery — unchanged

`stacapi.py`'s `getdimensions` already unions `derive_bands(collection.id,
asset_facts, BAND_SOURCES)` into `cube:dimensions.spectral.values`.
`derive_bands` never consults `sibling` at all (a `resolve_band`-only
concern), so the two new entries are discovered automatically once merged
into `BAND_SOURCES` — no change to `stacapi.py`.

### 2.3 Production: two new readers, no GCPs

Two reader classes share a base (`Sentinel2AngleReader`), not a subclass of
`BandReader` (ADR 0002's SAR reader) — that base is documented and shaped
around GCP inverse-mapping, which Sentinel-2 imagery does not have:

- **`ViewAngleMeanReader`** (`viewZenithMean`/`viewAzimuthMean`) — parses
  `Mean_Viewing_Incidence_Angle_List`, computes the arithmetic mean of the
  13 `ZENITH_ANGLE` values and the circular mean of the 13 `AZIMUTH_ANGLE`
  values, broadcasts as a constant band.
- **`SunAngleGridReader`** (`sunZenithAngles`/`sunAzimuthAngles`) —
  reprojects each destination pixel center into the tile's own
  `HORIZONTAL_CS_CODE`, converts to grid row/col units relative to
  `(Geoposition.ULX, Geoposition.ULY)`, and bilinearly interpolates the
  23×23 `Sun_Angles_Grid` via `Grid2D.interp` — a generic rectilinear-grid
  bilinear interpolator, nothing about its implementation is SAR-specific.
  It moved from `sar/annotation.py` to the top-level `grid2d.py` as part of
  this ADR (unchanged implementation, `sar/annotation.py` now imports it
  from there) rather than having Sentinel-2 code import a module named
  `sar` for a component neither collection owns.

Both parse and cache `MTD_TL.xml` via a new
`titiler/openeo/sentinel2/tile_metadata.py`, mirroring
`sar/annotation.py`'s `get_calibration`/`get_noise` shape exactly
(`cachetools` + `condition=` single-flight, keyed by href), fetched via the
existing `sar.fetcher.get_default_fetcher()` — unchanged for CDSE (S3) and
Earth Search (public HTTPS).

### 2.4 Read-path rules — inherited unchanged from ADR 0002 §2.4

Mask inheritance, resolution estimation, and pixel-limit counting all key
generically off `ResolvedBand.sibling_key` in `reader.py`, already built for
S1 and requiring no change: `_get_reader`, `_get_derived_asset_info`,
`_get_asset_info`, and `_inherit_derived_band_masks`'s bodies are unchanged.
The only diff in `reader.py` is building `sibling_candidates` (item asset
facts including `gsd`) at the two spots that call `resolve_band`, and
threading it through.

### 2.5 Band surface

| Band | XML source | Computation | Shape |
| --- | --- | --- | --- |
| `viewZenithMean` | `Mean_Viewing_Incidence_Angle_List` (13 entries) | arithmetic mean of `ZENITH_ANGLE` | scalar, broadcast |
| `viewAzimuthMean` | same | circular mean of `AZIMUTH_ANGLE` | scalar, broadcast |
| `sunZenithAngles` | `Sun_Angles_Grid/Zenith` (23×23, 5000 m step) | bilinear interpolation | per-pixel grid |
| `sunAzimuthAngles` | `Sun_Angles_Grid/Azimuth` | bilinear interpolation | per-pixel grid |

### 2.6 No convergence/planner needed

ADR 0002 §2.6's reader-requirement planner exists so `sar_backscatter` can
consume LUT bands the user never asked for by name. No process in this
codebase needs these four bands auto-injected — they are requested directly
via `load_collection(bands=[...])`, exactly like S1's LUT bands already can
be. `reader_requirements.py` and its `factory.py` wiring are untouched by
this ADR.

---

## 3. Consequences

**Gained.** `viewZenithMean`, `viewAzimuthMean`, `sunZenithAngles`,
`sunAzimuthAngles` become ordinary cube bands for `sentinel-2-l2a`, sourced
from the real measured geometry rather than a coarse per-tile
approximation, closing PR #281. `registry.py`'s callable-`sibling`
extension is reusable by any future band source whose logical sibling has
no catalogue-stable name. Zero behavior change to any existing S1 code path.

**Accepted costs.** Two reader classes and one XML-parsing module are new
surface. `viewZenithMean`/`viewAzimuthMean` are scalar-per-scene by design
(matching their name), not a spatially-varying reconstruction from the
per-band, per-detector `Viewing_Incidence_Angles_Grids` — a deliberately
coarser choice than `sunZenithAngles`/`sunAzimuthAngles`'s real grid,
justified because SentinelHub's own naming convention already draws this
exact distinction ("Mean" vs "Angles").

**Deliberately not done.**
Reconstructing `viewZenithMean`/`viewAzimuthMean` from the per-band spatial
grids instead of the scalar list; any change to `sar_backscatter` or the
S1 band-sources code path; a config or entry-point mechanism for these
bands (ADR 0002 §2.1's rule against band-source-specific plugin systems
applies here unchanged).

### 3.1 Open risks

- **Planetary Computer is unsupported, not silently wrong.** Confirmed live
  (`HTTP 409 PublicAccessNotPermitted`) on an anonymous fetch of PC's
  `granule-metadata` asset; this codebase has no SAS-token signing
  mechanism anywhere today (grepped for `planetary`/`sas_token`/`PC_SDK`/
  `modifier=` — none exist), and ADR 0001 §7.6 already recorded the same
  class of gap for SAR's annotation XML on this catalogue. Discovery still
  advertises all four bands for any `sentinel-2-l2a` collection (§2.2 is
  catalogue-agnostic by construction), so a PC-backed deployment's
  `/collections` response is accurate to the STAC contract; *reading* one
  of these bands against PC raises a clear fetch error, not a wrong value.
  A SAS-signing `AssetFetcher` is a reasonable follow-up but is out of
  scope here.
- **XML schema-version drift.** `MTD_TL.xml`'s namespace URI is versioned
  by ESA processing baseline (observed at least PSD-14/PSD-15 in the wild).
  The parser matches child elements by local tag name only, ignoring the
  namespace, to avoid hardcoding one version — mirroring
  `sar/annotation.py`'s own handling of the noise-schema version split
  (IPF ≥ 2.90 vs legacy). If a future baseline renames an element outright
  rather than just changing its namespace, parsing raises a clear
  `ValueError` naming the missing element rather than silently returning
  wrong data.
- **`Grid2D` moved, not cross-imported.** An earlier draft of this ADR had
  `sentinel2/tile_metadata.py` import `Grid2D` directly from
  `sar/annotation.py`, avoiding duplicating correctness-sensitive
  bilinear-interpolation math at the cost of a non-SAR module depending on
  one named `sar`. Moving it to `bandsources/grid2d.py` was tried next and
  rejected: `bandsources/__init__.py` imports both the `sar`- and
  `sentinel2`-rooted readers, so a module living inside that package forces
  the whole package to initialize first, which is circular with
  `sentinel2.tile_metadata`'s own dependency on it — confirmed empirically
  (`ImportError: cannot import name 'TileMetadata' from partially
  initialized module`) when `sentinel2.tile_metadata` is the first thing
  imported. Resolved by moving `Grid2D` to a top-level `grid2d.py`
  (implementation unchanged) — both `sar/annotation.py` and
  `sentinel2/tile_metadata.py` now import it from a location neither owns
  and which owns no package `__init__.py` of its own to trigger, and
  `sar/annotation.py`'s own public `Grid2D` export is unaffected.

---

## 4. Related

- [ADR 0001 — SAR backscatter](0001-sar-backscatter.md) §7.6 (Planetary
  Computer's pre-existing SAS-token gap for XML metadata assets).
- [ADR 0002 — Band sources](0002-band-sources.md) (the registry/reader
  mechanism this ADR extends).
- [PR #281](https://github.com/sentinel-hub/titiler-openeo/pull/281) —
  superseded; closed by this work.
