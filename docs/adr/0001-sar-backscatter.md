# ADR 0001 — SAR backscatter (`sar_backscatter`, `ard_normalized_radar_backscatter`)

- **Status:** Proposed
- **Date:** 2026-07-28
- **Deciders:** @emmanuelmathot
- **Supersedes / superseded by:** —

---

## 1. Context

### 1.1 The ask

Implement openEO SAR backscatter processing in titiler-openeo, targeting
`ard_normalized_radar_backscatter` and modelled on the Sentinel Hub Sentinel-1 GRD
processing options. Preference is to start with a from-scratch, non-terrain-corrected
(ellipsoid) implementation and to avoid heavy dependencies such as Orfeo ToolBox
unless unavoidable.

### 1.2 What the openEO specs actually require

Both processes are **experimental** in the openEO process catalogue.

`sar_backscatter(data, coefficient, elevation_model, mask, contributing_area,
local_incidence_angle, ellipsoid_incidence_angle, noise_removal, options)` where
`coefficient` ∈ `{beta0, sigma0-ellipsoid, sigma0-terrain, gamma0-ellipsoid,
gamma0-terrain, null}`, default `gamma0-terrain`. Returned values are in **linear
scale**. The spec mandates **bilinear interpolation** for both DEM and backscatter
resampling.

Crucially, `ard_normalized_radar_backscatter` is **not an independent algorithm**. Its
spec ships a `process_graph` that is a thin wrapper:

```json
{ "nrb": { "process_id": "sar_backscatter", "arguments": {
    "data": {"from_parameter": "data"},
    "coefficient": "gamma0-terrain",
    "mask": true,
    "local_incidence_angle": true,
    "elevation_model": {"from_parameter": "elevation_model"},
    "contributing_area": {"from_parameter": "contributing_area"},
    "ellipsoid_incidence_angle": {"from_parameter": "ellipsoid_incidence_angle"},
    "noise_removal": {"from_parameter": "noise_removal"},
    "options": {"from_parameter": "options"} }, "result": true } }
```

**Consequence:** `ard_normalized_radar_backscatter` is, by definition, radiometrically
terrain-flattened gamma0 plus a DEM-derived local incidence angle band, with CARD4L
metadata. An ellipsoid-only implementation **cannot** be published under that process
id without misrepresenting the product. `sar_backscatter` is the correct primitive to
build first; the ARD process becomes available for free (as a built-in UDP) once
`gamma0-terrain` exists.

The spec itself acknowledges the coupling to source products: _"backscatter computation
may require instrument specific metadata that is tightly coupled to the original SAR
products. As a result, this process may only work in combination with loading data from
specific collections, not with general data cubes."_ This licenses a design where
`sar_backscatter` is fused with `load_collection` rather than operating on an arbitrary
cube.

### 1.3 Reference implementations

| Implementation | Coefficients supported | Engine | Notes |
| --- | --- | --- | --- |
| [openeo-geopyspark-driver](https://github.com/Open-EO/openeo-geopyspark-driver/blob/master/openeogeotrellis/collections/s1backscatter_orfeo.py) (VITO/Terrascope) | `beta0`, `sigma0-ellipsoid`, `gamma0-ellipsoid` **only** | Orfeo ToolBox (`SARCalibration` + `OrthoRectification`) | `mask`, `contributing_area`, `local_incidence_angle`, `ellipsoid_incidence_angle` all raise `FeatureUnsupportedException`. Restricted to `IW_GRDH_1S*`. DEM used for **geometric** orthorectification only. |
| Sentinel Hub | `BETA0`, `SIGMA0_ELLIPSOID`, `GAMMA0_ELLIPSOID` (default), `GAMMA0_TERRAIN` | Proprietary | `orthorectify` (bool, default false), `demInstance` ∈ `{MAPZEN, COPERNICUS, COPERNICUS_30, COPERNICUS_90}`, `speckleFilter` ∈ `{NONE, LEE(windowSizeX/Y 1–7)}`. RTC only when `GAMMA0_TERRAIN` + orthorectify, using area-integration with a DEM oversampling factor (default 2). |

The most-used production openEO backend ships **ellipsoid coefficients only**. That is
strong precedent for the staged approach proposed here, and it means an ellipsoid-only
titiler-openeo is at feature parity with the reference driver rather than behind it.

Two distinctions must be kept separate throughout, because Orfeo and Sentinel Hub
conflate them in their option names:

- **Geometric** terrain correction (orthorectification) — where a pixel is placed on the
  ground. Needs a DEM. Fixes planimetry, not radiometry.
- **Radiometric** terrain correction / flattening — the normalising reference area per
  pixel. This is what turns `gamma0-ellipsoid` into `gamma0-terrain`, and it is the only
  thing CARD4L NRB cares about beyond metadata.

### 1.4 Constraints from titiler-openeo's architecture

- Processing is **on-demand and tile-scoped**. There is no batch product-level stage, no
  persistent scratch directory, and no place to hang a multi-minute SNAP/Orfeo graph.
- [`RasterStack`](../../titiler/openeo/processes/implementations/data_model.py) is lazy:
  `load_collection` builds `rio_tiler.tasks.create_tasks(_reader, items, …)` and defers
  execution. The stack carries everything a SAR reader needs —
  `_tasks` (each paired with its STAC item dict), `width`, `height`, `bounds`,
  `dst_crs`, `band_names`.
- The standard read path is
  [`SimpleSTACReader`](../../titiler/openeo/reader.py), which assumes assets are
  georeferenced rasters. **A Sentinel-1 GRD measurement TIFF is not** — see §1.6.
- Output is a `RasterStack` of `ImageData`; processes are plain functions auto-registered
  from `titiler/openeo/processes/implementations/` against a spec JSON in
  `titiler/openeo/processes/data/`.

### 1.5 Input data availability (verified 2026-07-28)

All three STAC catalogues the project targets expose **per-polarisation measurement
GeoTIFFs _and_ the calibration/noise annotation XML as first-class STAC assets**:

| Catalogue | Data assets | Annotation assets | Access |
| --- | --- | --- | --- |
| CDSE (`stac.dataspace.copernicus.eu/v1`, `sentinel-1-grd`) | `vv`/`vh`/`hh`/`hv` (COG) + `Product` (zip) | `schema-calibration-*`, `schema-noise-*`, `schema-product-*`, `safe_manifest` | S3 `eodata` (creds in `.env.cdse`); full `.SAFE` layout also on S3 |
| Earth Search (`earth-search.aws.element84.com/v1`, `sentinel-1-grd`) | `vv`/`vh`/`hh`/`hv` (COG) | same set | AWS eu-central-1, **requester-pays** |
| Planetary Computer (`sentinel-1-grd`) | `vv`/`vh`/`hh`/`hv` (COG) | same set | Azure blob + SAS token |

This is the decisive enabler: **no SAFE unzipping, no Orfeo, no product download is
required.** Everything needed for ellipsoid calibration is individually addressable over
HTTP range requests.

### 1.6 Empirical findings

Measured against live Planetary Computer data (`S1C_EW_GRDM_1SSH_20260728T111431…` and
`S1C_IW_GRDH_1SSH_20260728T111408…`) with the project's own rasterio 1.5.0 / GDAL 3.12.1.

**(a) Measurement TIFFs are GCP-referenced COGs.**
`uint16` DN, `crs=None`, identity transform, **462 GCPs** (22 × 21 grid) for EW GRDM /
**147 GCPs** (7 × 21) for IW GRDH, `gcp_crs=EPSG:4326`, GCP _z_ ≈ 0 (ellipsoid heights).
Internally tiled 1024 × 1024 with overviews `[2, 4, 8, 16, 32, 64]`. A
`WarpedVRT(src, src_crs=gcp_crs, crs="EPSG:4326")` opens and reads correctly
(256 × 256 whole-scene read in **0.5 s**).

**(b) The default GDAL GCP transformer is not accurate enough.** Residuals of the
transformer evaluated back at the GCPs themselves:

| Transformer | EW GRDM (40 m px) | IW GRDH (10 m px) |
| --- | --- | --- |
| Polynomial order 1 | RMS 4529 m / max 12035 m | — |
| Polynomial order 2 | RMS 203 m / max 633 m | — |
| Polynomial order 3 | RMS 10 m / max 36 m | — |
| **rasterio/GDAL default** | **RMS 205 m / max 642 m** | **RMS 25 m / max 77 m** |
| **Thin-plate spline (`tps=True`)** | exact at GCPs | **RMS 0.2 m / max 0.3 m** |

GDAL's auto-selected order is 2. On IW GRDH that is a **2.5-pixel RMS, 8-pixel worst-case
geolocation error introduced purely by the warp** — before any terrain effect. TPS (or an
explicitly forced order 3) is mandatory.

**`rasterio.warp.reproject` cannot deliver TPS.** Its `**kwargs` are documented as being
forwarded to `GDALCreateGenImgProjTransformer2`, and `MAX_GCP_ORDER` genuinely is — but
`METHOD` is silently ignored. Warping the same window five ways and diffing the arrays:

| | default | `METHOD=GCP_TPS` | `METHOD=GCP_POLYNOMIAL` | `MAX_GCP_ORDER=2` | `MAX_GCP_ORDER=3` |
| --- | --- | --- | --- | --- | --- |
| mean abs diff vs default | 0.00 | **0.00** | **0.00** | **0.00** | 1.34 |

`GCP_TPS`, `GCP_POLYNOMIAL`, order 2 and the default are all **bit-identical**; only
`MAX_GCP_ORDER` changes anything. So `reproject` is unconditionally on the order-2
polynomial, and asking it for TPS produces a silently wrong result — the most dangerous
possible failure mode, since it looks like it worked. (Found by the Phase 0 prototype,
after this ADR initially recommended exactly that call.)

**Consequence for the design:** do not delegate the geocoding transformer to GDAL at all.
Build the inverse map once with `GCPTransformer(gcps, tps=True).rowcol(...)` and use that
_same_ map both to sample the DN and to evaluate the LUTs (§7.3). This is exact, removes
the dependency on undocumented transformer-option plumbing, and makes it structurally
impossible for the radiometry and the geometry to be sampled on different geometries.
Measured cost on a 384 × 384 tile: 0.45 s for the inverse map, 0.50 s for the decimated
read — _faster_ than the 1.84 s `reproject` call it replaces.

**(c) Decimated / overview reads are radiometrically safe.** SAR intensity averaging must
happen in power (DN²), not amplitude (DN). Theory predicts up to −1.05 dB bias for
single-look data. Measured on real GRD:

| Decimation | EW GRDM | IW GRDH |
| --- | --- | --- |
| ×2 | −0.02 dB | −0.04 dB |
| ×4 | −0.02 dB | −0.06 dB |
| ×8 | −0.01 dB | −0.01 dB |

GRD products are already multi-looked _and_ spatially oversampled (IW GRDH: 10 m spacing
vs ~20 m resolution), so neighbouring pixels are strongly correlated and the
amplitude/power averaging distinction collapses. **The COG overviews can be used
directly**, which is what makes a tile-server implementation viable at low zoom. (Re-verify
if SLC or single-look products are ever added.)

**(d) The calibration LUTs give all three ellipsoid coefficients for free.**
`calibration-*.xml` (956 KB, **5 ms** to parse with stdlib ElementTree) contains 63
`calibrationVector`s × 261 samples, each with `sigmaNought`, `betaNought`, `gamma` **and**
`dn`. `betaNought` is constant (1030.932) as expected for GRD.

The three LUTs are mutually consistent to **2 × 10⁻⁴ degrees**, which means the
**ellipsoid incidence angle is recoverable analytically with no orbit geometry at all**:

```
θ_ell = arccos((A_γ / A_σ)²)  ≡  arcsin((A_β / A_σ)²)
```

Verified across the swath: 18.15° (near range) → 45.52° (far range), the two formulas
agreeing to 0.0002°. So `ellipsoid_incidence_angle=true` is deliverable in the
ellipsoid-only phase.

ESA's calibration technical note ([ESA-EOPG-CSCOP-TN-0002][esa-cal]) confirms this is by
construction rather than coincidence — it defines
`A_σ = √(A_dn²·K / sin α)`, `A_β = √(A_dn²·K)`, `A_γ = √(A_dn²·K / tan α)` and states:
_"With the introduction of the calibration LUT, it is not necessary to compute anymore
the incidence angle as it is built-in the LUT. However if necessary it can simply be
retrieved by: cos(α) = A_γ² / A_σ²."_

[esa-cal]: https://sentinels.copernicus.eu/documents/247904/685163/S1-Radiometric-Calibration-V1.0.pdf

**(e) Thermal noise LUTs are the modern IPF ≥ 2.9 format.** `noise-*.xml` (422 KB) has
`noiseRangeVectorList` (64 × 267, in DN² units) and `noiseAzimuthVectorList` (31
per-swath blocks with `firstAzimuthLine`/`lastAzimuthLine`/`firstRangeSample`/
`lastRangeSample` and a scaling LUT). Full noise is
`η(l,p) = noiseRangeLut(l,p) × noiseAzimuthLut(l)` within each swath block.

**(f) GDAL's `SENTINEL1_CALIB:` shortcut is not usable on cloud STAC layouts.** GDAL's
SAFE driver supports `SENTINEL1_CALIB:{SIGMA0,BETA0,GAMMA,UNCALIB}:<safe>:<swath>:AMPLITUDE`,
which would apply calibration natively. Against Planetary Computer it fails with
`Measurement bands not found`: the manifest references
`./measurement/s1c-ew-grd-hh-20260728t111431-…-001.tiff` while the catalogue stores
`measurement/ew-hh.tiff`. Catalogue-normalised filenames break the driver. CDSE's
`/vsis3/eodata/…​.SAFE/` preserves original names so it would likely work **there only** —
untested (needs CDSE credentials). It also performs **no thermal noise removal**. Not a
portable foundation.

**(g) The noise annotation has two incompatible schemas, and the CDSE archive contains
both.** Bracketed by fetching manifests and noise XML across the archive:

| Acquisition | IPF | Noise schema |
| --- | --- | --- |
| 2015-06-04 | 002.43 | `noiseVectorList` → `noiseLut` |
| 2016-06-01 | 002.71 | `noiseVectorList` |
| 2017-06-01 | 002.82 | `noiseVectorList` |
| 2018-02-01 | 002.84 | `noiseVectorList` |
| **2018-04-01** | **002.90** | **`noiseRangeVectorList` + `noiseAzimuthVectorList`** |
| 2019-06-01 | 002.91 | `noiseRangeVectorList` + `noiseAzimuthVectorList` |

The cutover is IPF 2.90 (deployed 2018-03). Pre-2.90 products have a single range-only
noise LUT and **no azimuth descalloping vector at all**. A parser written against only the
modern schema raises on every product before March 2018 — which is 3.5 years of the
archive. `noise_removal=true` (the spec default) must therefore handle both, or the
process fails outright on historical data.

Note this is orthogonal to the raster format: CDSE's `sentinel-1-grd` is **100 % COG back
to 2015** (sampled 200 items in each of 2015/2017/2019/2021/2023/2025/2026 — every one
carries the `_COG` id suffix). The archive is homogeneous in pixels and heterogeneous in
annotations.

**(h) End-to-end validated against CDSE, the reference catalogue.** Phase 0 run on
`S1C_IW_GRDH_1SDV_20260728T150953…_COG` (IW GRDH VV, 25494 × 16639, 210 GCPs, `_COG`
overviews), authenticating with `AWS_PROFILE=cdse` +
`AWS_S3_ENDPOINT=eodata.dataspace.copernicus.eu` + `AWS_VIRTUAL_HOSTING=FALSE`:

```text
LUTs      calibration (27, 639)                fetch+parse 1.04 s
read      window 9477x9342 -> 394x389 (x24)    0.43 s   inverse TPS map 0.22 s
noise     subtracted; 0.48 % of valid pixels clamped at 0
sigma0-ellipsoid   p05 -29.96 / median -23.02 / p95 -21.59 dB   (146 738 px)
incidence 35.39-41.00 deg, LUT identity holds to 0.00008 deg
total     3.2 s
```

Median **−23.0 dB** over the Barents Sea is the expected magnitude for open-water VV at
~38° incidence, and the 0.48 % clamped pixels are exactly where thermal noise dominates —
so `noise_removal` is doing real work rather than being a no-op. The 384 × 384 tile in
3.2 s cold meets the §7.8 latency budget with margin. No `proj:*` warning fired, as
expected for CDSE (§1.7).

### 1.7 Catalogue dependence

`sar_backscatter` is unusually tightly coupled to the catalogue — the openEO spec says so
outright (§1.2). Unlike `ndvi`, it depends not just on pixels but on **which sibling
assets exist, how they are keyed, how they are addressed, and what the item metadata
claims**. All four vary between catalogues, and the variation is not cosmetic.

Compared field by field on one GRD item from each catalogue (all verified, 2026-07-28):

| | CDSE | Earth Search | Planetary Computer |
| --- | --- | --- | --- |
| Data asset keys | `vv`/`vh`/`hh`/`hv` | same | same |
| Calibration / noise keys | `schema-calibration-<pol>`, `schema-noise-<pol>` | same | same |
| Manifest key | `safe_manifest` (**underscore**) | `safe-manifest` (**hyphen**) | `safe-manifest` |
| Extra assets | `Product` (zip) | — | `tilejson`, `rendered_preview` |
| `product:type` | `IW_GRDH_1S`, `IW_GRDH_1S_B`, `EW_GRDM_1S`, … | absent | absent |
| `sar:product_type` | absent | `GRD` | `GRD` |
| `sar:instrument_mode` / `sar:polarizations` | yes | yes | yes |
| Item `proj:*` | **absent** | `proj:epsg`/`transform`/`shape`/`bbox`/`centroid` | **same set as Earth Search** |
| Asset `proj:*` | `proj:shape`, `proj:code` (null) | — | — |
| Nodata declaration | asset `nodata: 0`, `data_type` | `raster:bands[].nodata` | — |
| Storage / auth extensions | `storage:schemes`, `storage:refs`, `auth:refs`, `alternate:name` | `storage:platform`, `storage:region`, `storage:requester_pays` | — |
| Href scheme | `s3://eodata/…` + `alternate.https` (OData, 401) | `s3://` requester-pays | `https://` + SAS |
| Original SAFE layout | **yes, only one** — `…​_COG.SAFE/annotation/calibration/calibration-s1c-ew-grd-hh-…-001-cog.xml` | no — flattened to `…/annotation/calibration/calibration-ew-hh.xml` | no — same flattening as Earth Search |

Two rows deserve emphasis because they invert assumptions that look safe:

**No product-type field is universal, and the two that exist disagree in granularity.**
CDSE publishes `product:type` with the full identifier (`EW_GRDM_1S`); Earth Search and
Planetary Computer omit it and instead publish the SAR extension's `sar:product_type`,
whose value is just `GRD`. A whitelist of full product-type strings — as the geopyspark
driver uses (§1.3) — therefore works on CDSE and rejects the other two outright.

Support must key off **capability rather than identity**: the real requirement is that the
calibration and noise annotation siblings resolve for the requested polarisation. If they
do, the item is processable whatever its metadata says; if they do not, it is not,
whatever its metadata says. Product type is still worth consulting — accept a `GRD` match
from either field, treat absence as acceptable, and reject positively-known-unsupported
types (`SLC`, `OCN`) — but as a sharpener for error messages, not as the gate.
`sar:instrument_mode` and `sar:polarizations` are present on all three catalogues and are
the reliable way to enumerate polarisations.

**Earth Search and Planetary Computer both publish a fabricated `proj:transform`.** Each
advertises `proj:epsg: 4326` with a north-up affine obtained by dividing a bbox by the
pixel dimensions. For a product in SAR geometry — a rotated, non-rectangular swath —
**that transform is fiction**. Run through the project's own code path:

```text
_extract_proj_info(cdse_item)          -> None  (falls back to footprint bbox)

_extract_proj_info(earth_search_item)  -> crs=EPSG:4326, 10062x10331
    transform      = (0.004958, 0, 66.4585, 0, -0.000500, 86.9813)
    bbox-derived   = (0.004722, 0, 67.8629, 0, -0.000497, 86.9596)   # from proj:bbox

_extract_proj_info(mspc_item)          -> crs=EPSG:4326, 10203x10400
    transform      = (0.001055, 0, 120.7579, 0, -0.000482, -59.9322)
    bbox-derived   = (0.001055, 0, 120.7579, 0, -0.000482, -59.9322)  # identical
```

Planetary Computer's is bbox-derived to the last digit. Its `proj:shape` is also
**transposed** relative to the actual raster (it implies 10203 × 10400; the TIFF is
10400 × 10203).

So `SimpleSTACReader` accepts that transform and a plain `load_collection` on an Earth
Search **or** Planetary Computer S1 GRD item returns a **silently mis-georeferenced image,
with no error raised**. CDSE yields no proj info at all — also not correct, but it at
least asserts nothing false, and the footprint-bbox fallback is honest about being an
approximation.

**Two consequences for the design:**

1. `sar_backscatter` must **ignore item and asset `proj:*` entirely** for GRD input and
   always take the GCPs from the measurement TIFF itself. This must be an explicit,
   tested rule, not an accident of implementation order.
2. There is a **latent bug today**, independent of this ADR: `load_collection` on
   Sentinel-1 GRD from Earth Search or Planetary Computer produces wrong geometry. Worth
   raising separately.

**CDSE is the right reference catalogue.** Its archive is homogeneously COG-ified back to
at least 2020 (`…_COG` item ids, `-cog.tiff` measurement files, internally tiled with
overviews), it carries the complete annotation asset set with consistent keys throughout,
it preserves the original SAFE layout on S3, and it self-describes access via the
`storage` and `authentication` STAC extensions — so a fetcher can be driven from
`storage:refs` → `storage:schemes` rather than hard-coded. It asserts no false projection
metadata.

**Portability is a tracked property, not an assumption.** The capability a catalogue must
provide should be stated as an explicit contract — measurement asset per polarisation,
calibration and noise annotation siblings, GCP-carrying TIFF, no misleading `proj:*` — and
verified per catalogue by a test, so that "works on Earth Search" is a claim backed by
evidence rather than hope. Catalogue support is expected to be **declared**, not inferred:
an unverified catalogue should be rejected with a clear message rather than silently
producing plausible-looking wrong numbers.

---

## 2. Decision drivers

1. **Honesty of the product label.** A process id carries a scientific claim. Publishing
   ellipsoid data as `ard_normalized_radar_backscatter` would be wrong.
2. **Dependency weight.** Orfeo/SNAP are ~GB-scale, hard to containerise, and process
   whole products — a poor fit for a per-tile server.
3. **Latency budget.** Tile requests must stay in the low seconds.
4. **Architectural fit.** Must preserve `RasterStack` laziness and the existing
   bbox/width/height/CRS contract.
5. **Incremental value.** Ellipsoid backscatter already unlocks water/flood mapping,
   agriculture, sea ice and oil-spill use cases over low-relief terrain.

---

## 3. Options

### Option A — Do not implement; serve pre-computed RTC collections

Point users at [OPERA RTC-S1](https://hyp3-docs.asf.alaska.edu/guides/opera_rtc_product_guide/)
(global ex-Antarctica, 30 m, Copernicus GLO-30, gamma0 RTC, COG, free via ASF/AWS) or
Planetary Computer `sentinel-1-rtc`. These are already map-projected COGs — titiler-openeo
reads them today with **zero SAR code**.

- ✅ Zero effort, zero risk, genuinely CARD4L-grade science.
- ❌ Fixed 30 m grid, IW only, latency of the upstream production chain, no control over
  parameters, does not answer "implement `sar_backscatter`".

### Option B — Native ellipsoid-only `sar_backscatter` **(recommended Phase 1)**

Implement `beta0`, `sigma0-ellipsoid`, `gamma0-ellipsoid` (+ `null`) from the calibration
and noise LUTs, geocoded via GCP/TPS warping. `mask` and `ellipsoid_incidence_angle`
supported; terrain coefficients and DEM-derived bands raise a clear error.

- ✅ **Zero new runtime dependencies** (rasterio + numpy + stdlib XML, all present).
- ✅ Radiometrically exact — identical LUT and formula as SNAP's Calibration operator.
- ✅ Beats the reference geopyspark driver, which cannot do `ellipsoid_incidence_angle`.
- ✅ Tile-scoped and lazy; fits the architecture without changing it.
- ❌ No terrain correction, geometric or radiometric. Not CARD4L.
- **Effort: ~2–3 person-weeks** including tests and docs.

### Option C — Option B + DEM-based geometric orthorectification (Range–Doppler)

Adds a real DEM and solves the Range–Doppler equations per output pixel using the orbit
state vectors in the product annotation XML. Radiometry stays ellipsoid-based. **This is
exactly what Orfeo/geopyspark produces today**, and equals Sentinel Hub's
`GAMMA0_ELLIPSOID + orthorectify=TRUE`.

- ✅ Removes the Δh/tan(θ) planimetric error — the single biggest defect of Option B.
- ✅ Reaches parity with the reference backend's best output.
- ❌ Needs orbit interpolation, zero-Doppler iteration, geoid handling, DEM tiling
  strategy, and a DEM cache. UTC leap seconds are a known trap (sarsen documents crashes).
- **Effort: ~4–6 person-weeks** on top of B.

### Option D — Option C + radiometric terrain flattening (true `gamma0-terrain`, CARD4L)

Implements flattening-gamma area integration (Small 2011), plus `local_incidence_angle`,
`contributing_area`, layover/shadow masking, and CARD4L metadata. This is what unlocks
`ard_normalized_radar_backscatter`.

- ✅ The only path to an honest `ard_normalized_radar_backscatter`.
- ❌ Substantially harder; correctness is difficult to defend without formal validation
  against SNAP/OPERA. DEM oversampling makes it the most expensive step per tile — a poor
  fit for interactive tiles.
- **Effort: ~8–12 person-weeks** on top of C, plus a validation campaign.

### Option E — Delegate terrain correction to [`sarsen`](https://github.com/bopen/sarsen)

Pure-Python (xarray/dask/rioxarray), Apache-2.0, actively developed, supports GRD and SLC
in SM/IW/EW, produces both GTC and RTC gamma0.

- ✅ Avoids Orfeo entirely while still reaching `gamma0-terrain`.
- ✅ Cloud-native design; algorithms peer-reviewed.
- ❌ Self-declared **beta**; "documentation needs improvement"; **UTC leap seconds
  unsupported, may crash or give wrong results**; no published validation against SNAP.
- ❌ Pulls in xarray + dask + rioxarray + xarray-sentinel — a significant dependency
  increase for a service that is currently numpy/rasterio only.
- ❌ Its API is product-oriented (whole SAFE → DEM grid), not tile-oriented; wiring it
  into per-tile requests is non-trivial.
- **Effort: ~3–5 person-weeks** to integrate, with ongoing exposure to upstream beta risk.

### Option F — Orfeo ToolBox, as the geopyspark driver does

- ✅ Battle-tested in production openEO.
- ❌ Heavy native dependency; subprocess-per-tile is the "Version 1" design that the
  reference driver itself abandoned as too slow. Still only reaches ellipsoid coefficients.
- ❌ Contradicts the stated preference. **Not recommended.**

### Option G — Proxy to Sentinel Hub Process API

- ✅ Immediate full feature set including `GAMMA0_TERRAIN`.
- ❌ Commercial dependency and per-request cost; makes titiler-openeo a thin proxy;
  contradicts the project's self-contained model. **Not recommended.**

### Comparison

| Option | Effort | New deps | Radiometry | Geometry | CARD4L | Tile latency |
| --- | --- | --- | --- | --- | --- | --- |
| A | 0 | none | reference-grade | terrain | yes (upstream) | best |
| **B** | **2–3 pw** | **none** | **exact (ellipsoid)** | **ellipsoid** | **no** | **good** |
| C | +4–6 pw | DEM collection | exact (ellipsoid) | terrain | no | moderate |
| D | +8–12 pw | DEM collection | terrain-flattened | terrain | yes | poor |
| E | 3–5 pw | xarray, dask, rioxarray, xarray-sentinel | terrain-flattened | terrain | close | poor–moderate |
| F | 3–4 pw | Orfeo (native, GB) | exact (ellipsoid) | terrain | no | poor |
| G | 1–2 pw | commercial API | terrain-flattened | terrain | yes | network-bound |

---

## 4. Decision

**Adopt a staged path: B → C → D, with A available immediately as the documented answer
for users who need CARD4L-grade gamma0 today.**

**CDSE is the reference catalogue** (§1.7). Phase 1 targets it first and is verified
against it. Other catalogues are added later as _declared, tested_ support rather than
assumed portability.

**Phase 0 (first): a throwaway prototype against a single CDSE GRD item.** Written and
runnable at [`scripts/sar_backscatter_prototype.py`](../../scripts/sar_backscatter_prototype.py)
(`--catalog cdse` by default; `--catalog mspc` runs credential-free while CDSE access is
being sorted out). Deliberately unstructured — one file, no abstractions. Its only job is
to convert open questions into measurements before any contract is signed. It has already
paid for itself: it caught that §7.3's originally-recommended `reproject(METHOD=GCP_TPS)`
call silently does nothing (§1.6b), which would have shipped a ~200 m geolocation error
that no unit test of the calibration maths would ever have found. It closes, or sharply
narrows, most of §9:

| Open question | How the prototype settles it |
| --- | --- |
| §9.1 fetcher | Does `obstore` (or whatever) actually pull the annotation XML from CDSE's custom S3 endpoint with the credentials in `.env.cdse`? Binary answer, one afternoon. |
| ~~§9.3 transformer plumbing~~ | **Already settled**: `METHOD` is ignored by both `WarpedVRT` and `reproject`; only `MAX_GCP_ORDER` is honoured. Design changed to own the inverse map (§7.3). |
| §9.4 `absoluteCalibrationConstant` | Compare prototype σ⁰ against a SNAP-calibrated reference for the same item. A uniform ~0.46 dB offset means it must be applied separately. This is the highest-value single measurement in the list. |
| §9.5 collection whitelist | Enumerate `product:type` across a CDSE date range and see what actually occurs (`IW_GRDH_1S`, `IW_GRDH_1S_B`, `_C`, `EW_GRDM_1S`, …). |
| §9.7 border noise | Inspect swath edges on a pre-2018 vs a recent item and see whether the artefact is visible at all. |
| §7.5 caching, §7.8 latency | Measure real XML fetch and warp times to size the cache and confirm the < 3 s budget is realistic. |

The prototype also validates the §7.3 algorithm end to end — LUT interpolation in
destination space, TPS warp, noise subtraction — cheaply enough to discard and rewrite if
the shape is wrong. **Do not evolve it into the implementation.** Its output is a set of
numbers written back into this ADR, plus a golden reference tile for §7.9.

**Phase 1:** implement `sar_backscatter` natively, supporting
`beta0`, `sigma0-ellipsoid`, `gamma0-ellipsoid` and `null`. `noise_removal` (default
`true`), `mask` and `ellipsoid_incidence_angle` are supported. `sigma0-terrain` and
`gamma0-terrain` raise `ProcessParameterInvalid`; `contributing_area` and
`local_incidence_angle` raise `FeatureUnsupported`. Do **not** register
`ard_normalized_radar_backscatter`.

**Phase 2:** geometric orthorectification (Option C), reusing the DEM through the existing
`load_collection` machinery. Re-evaluate `sarsen` at this point — if it has left beta and
gained leap-second handling, Option E may become cheaper than building C+D.

**Phase 3:** terrain flattening (Option D). Only then register
`ard_normalized_radar_backscatter`, as a built-in UDP using the spec's own
`process_graph`, so it needs no separate implementation.

Rationale: Phase 1 is cheap, dependency-free, radiometrically exact, and already matches
the most widely used openEO backend's capability. It also builds every piece Phases 2–3
need (asset resolution, LUT parsing and caching, geocoding, band assembly). The expensive,
scientifically contentious work is deferred until there is demand and a validation budget.

---

## 5. Scientific accuracy statement (Phase 1)

This must be reproduced in user-facing docs and in the process description.

**Radiometric accuracy — excellent.** The computation
`σ⁰ = (DN² − η) / A_σ²` is exactly the relation ESA specifies
([ESA-EOPG-CSCOP-TN-0002][esa-cal] §4), with the same supplied LUTs, and is what SNAP's
Calibration operator implements. The absolute calibration constant K is already folded
into the LUTs and must **not** be applied again (§9.4). Agreement with SNAP is expected to
be limited only by resampling choice, well inside Sentinel-1's ~0.3–0.5 dB absolute
radiometric accuracy. Decimation bias is ≤ 0.06 dB (§1.6c), and ESA explicitly endorses
averaging in power — _"the average backscatter coefficient over an area of interest can be
used instead: σ⁰ = ⟨DN²⟩ / A_σ²"_.

**A nuance on "ellipsoid".** The Earth model behind the LUTs is not the bare WGS84
ellipsoid: ESA defines it as _"the ellipsoid inflated with an average height"_ for the
scene. So `sigma0-ellipsoid` is already referenced to a mean scene elevation, which
slightly reduces — but does not remove — the terrain error below. The openEO coefficient
name `sigma0-ellipsoid` is still the correct label; the docs should just not overstate it
as "sea-level referenced".

**Geometric accuracy — terrain-dependent, and this is the real limitation.** The
geolocation grid is referenced to the WGS84 ellipsoid (GCP _z_ ≈ 0, §1.6a). Planimetric
error is:

```
Δx  =  Δh / tan(θ)
```

For IW (θ ≈ 29°–46°) that is **1.0 × to 1.8 × the terrain height above the ellipsoid
reference**. Concretely: 100 m of relief → 100–180 m of horizontal displacement; 500 m →
0.5–0.9 km. Over flat coastal or agricultural terrain the error is metres. Over mountains
it is catastrophic for any per-pixel analysis.

**Radiometric terrain effects — uncorrected.** On sloping terrain the illuminated area
per pixel departs from the ellipsoid assumption; `gamma0-ellipsoid` typically deviates
from `gamma0-terrain` by ±3 dB on moderate slopes and >6 dB on steep foreslopes. Layover
and shadow are neither detected nor masked.

**Fit for purpose:** open water and flood mapping, agriculture, wetlands, sea ice, oil
spill and ship detection, urban change over low-relief terrain, and any application that
compares same-geometry acquisitions (same relative orbit) where the terrain bias largely
cancels.

**Not fit for purpose:** mountainous terrain, cross-orbit (ascending vs descending)
comparison, biomass or soil-moisture retrieval, anything requiring CARD4L compliance.

---

## 6. Consequences

- Users asking for `ard_normalized_radar_backscatter` get a clear, actionable error
  pointing at `sar_backscatter` with ellipsoid coefficients **and** at the pre-computed
  RTC collections (Option A).
- A new subpackage becomes the home for SAR-specific I/O. This is the first process that
  legitimately bypasses `SimpleSTACReader`; the precedent needs to be documented so it is
  not copied casually.
- Annotation XML must be cached. Without caching, every 256 × 256 tile request downloads
  ~1.4 MB of XML per polarisation.
- The service must be able to fetch **non-raster** assets over the same credentialed
  transports as GDAL (S3 requester-pays, CDSE S3, Azure SAS). See open question §9.1.
- Collection metadata should advertise which collections `sar_backscatter` accepts, since
  the spec allows the process to be collection-specific.

---

## 7. Implementation contract — Phase 1

### 7.1 Module layout

```
titiler/openeo/sar/
  __init__.py
  annotation.py     # calibration/noise XML parsing → LUT objects
  calibration.py    # LUT interpolation + coefficient maths
  geocode.py        # GCP/TPS warp of DN into the destination grid
  reader.py         # per-item read: STAC item + dst grid → ImageData
titiler/openeo/processes/implementations/sar.py     # the `sar_backscatter` function
titiler/openeo/processes/data/sar_backscatter.json  # spec (copy of the openEO spec)
```

### 7.2 Process signature

```python
def sar_backscatter(
    data: RasterStack,
    coefficient: Optional[str] = "gamma0-terrain",
    elevation_model: Optional[str] = None,
    mask: bool = False,
    contributing_area: bool = False,
    local_incidence_angle: bool = False,
    ellipsoid_incidence_angle: bool = False,
    noise_removal: bool = True,
    options: Optional[Dict[str, Any]] = None,
) -> RasterStack: ...
```

Note the default is `gamma0-terrain` per spec, which Phase 1 rejects. This is intentional
and spec-compliant: callers must opt in explicitly to an ellipsoid coefficient. The error
message must say so plainly.

### 7.3 Algorithm (per item, per tile)

The process is a **task rewrite**, not a pixel operation. It consumes the _unrealised_
`RasterStack` produced by `load_collection`, reads `stack._tasks` (each carrying its STAC
item dict) together with `stack.width`, `stack.height`, `stack.bounds`, `stack.dst_crs`,
and returns a new lazy `RasterStack` whose task functions call the SAR reader. Laziness is
preserved end to end.

For each item and each requested polarisation:

1. Resolve the measurement asset (`vv`/`vh`/`hh`/`hv`) and its
   `schema-calibration-<pol>` / `schema-noise-<pol>` siblings, honouring
   `STAC_ALTERNATE_KEY` exactly as `reader.py` already does.
2. Open the measurement TIFF; read `src.gcps`. **Ignore item and asset `proj:*`
   entirely** — some catalogues advertise a bbox-derived affine that is fiction for
   SAR geometry (§1.7). The TIFF's own GCPs are the only trusted georeferencing.
3. Build the destination grid from `(bounds, crs, width, height)`.
4. **Build the inverse map, once.** `GCPTransformer(gcps, tps=True).rowcol(xs, ys)` on
   the destination pixel centres gives source `(line, pixel)` for every output pixel.
   This single array pair drives both of the next two steps, so the radiometry and the
   geometry cannot be sampled on different geometries. **Do not use
   `rasterio.warp.reproject`** — it silently ignores `METHOD=GCP_TPS` and runs an
   order-2 polynomial (§1.6b).
5. **Sample the DN.** Take the source window spanned by the inverse map (plus a small
   margin), read it decimated to roughly the destination sampling — GDAL serves this
   from the COG overviews, which §1.6c showed is radiometrically safe — then bilinearly
   sample it at the mapped coordinates. Bilinear per the openEO spec.
6. **Evaluate the LUTs at the same coordinates.** Bilinearly interpolate
   `A_σ`/`A_β`/`A_γ` and `η` on their rectilinear coarse grids (`np.interp` along each
   axis; no scipy). Exact, rather than warping a rasterised LUT.
7. **Calibrate.** `value = (DN² − η·noise_removal) / A²` with `A` selected by
   `coefficient`; `null` → `DN²` uncalibrated; `beta0` uses the constant `A_β`.
8. **Clamp.** Noise subtraction can drive values negative in low-backscatter areas; clamp
   to 0 and record the count (SNAP does the same). Do not emit negatives.
9. **Mask.** `DN == 0` marks border/no-data. Combine with the footprint cutline the
   existing `ImageRef` machinery already computes. Emit a `mask` band when `mask=true`.
10. **Extra bands.** When `ellipsoid_incidence_angle=true`, append
    `degrees(arccos((A_γ/A_σ)²))` (§1.6d).
11. Assemble `ImageData` with `band_descriptions` = polarisation names (lower-case, e.g.
    `vv`), `float32`, linear scale.

Validated end to end by the Phase 0 prototype
([`scripts/sar_backscatter_prototype.py`](../../scripts/sar_backscatter_prototype.py)):
384 × 384 tile in 4.1 s cold, of which 0.45 s inverse map + 0.50 s decimated read + 0.84 s
LUT fetch and parse.

### 7.4 Errors

| Condition | Error |
| --- | --- |
| `coefficient` ∈ {`sigma0-terrain`, `gamma0-terrain`} | `ProcessParameterInvalid` — "terrain-corrected coefficients are not supported; use `sigma0-ellipsoid` or `gamma0-ellipsoid`, or load a pre-computed RTC collection" |
| `contributing_area=true` or `local_incidence_angle=true` | `FeatureUnsupported` |
| `elevation_model` is not `null` | `DigitalElevationModelInvalid` (no DEM is used in Phase 1 — do not silently ignore) |
| `data` is not an unrealised S1 GRD `load_collection` stack | `ProcessParameterInvalid` naming the requirement |
| Missing calibration/noise asset | `ProcessParameterInvalid` naming the asset key |

### 7.5 Caching

LRU cache keyed on the resolved annotation-asset href, storing the **parsed** LUT arrays
(not the XML text). Sized in entries with a documented memory estimate; the parsed EW LUT
set is ~100 KB of float arrays vs 1.4 MB of XML. Must be thread-safe — `RasterStack`
executes tasks on a `ThreadPoolExecutor`.

### 7.6 Fetching the annotation assets

The measurement TIFF is read by GDAL, which resolves credentials from the environment
(`.env.cdse` sets `AWS_S3_ENDPOINT`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`,
`AWS_VIRTUAL_HOSTING=FALSE`). The annotation XML is **not** a raster, so it cannot ride
that same code path. The question is which client fetches it.

**Measured href schemes (2026-07-28):**

| Catalogue | annotation XML href | https alternate | Anonymous fetch? |
| --- | --- | --- | --- |
| CDSE | `s3://eodata/…​.SAFE/annotation/calibration/…​.xml` | OData `Products(uuid)/Nodes(…)/$value` | **HTTP 401** — needs a CDSE Keycloak OAuth2 bearer token |
| Earth Search | `s3://sentinel-s1-l1c/…` | none | **No** — `storage:requester_pays: true`, needs AWS SigV4 |
| Planetary Computer | `https://…blob.core.windows.net/…` | — | Only with a SAS token (free, anonymously mintable) |

**Two of the three catalogues serve the annotation XML over authenticated S3 only.** A
plain HTTP client covers Planetary Computer and nothing else. This is not a "just fetch
it over HTTP" problem.

CDSE self-describes its endpoint via the STAC `storage` extension
(`storage:schemes.cdse-s3.platform = https://eodata.dataspace.copernicus.eu`,
`requester_pays: false`), and Earth Search sets `storage:requester_pays`. A fetcher can
therefore be driven from item metadata rather than hard-coded per catalogue.

**Candidate fetchers:**

| Fetcher | New deps | Credential source | Notes |
| --- | --- | --- | --- |
| **GDAL VSI** (`/vsis3/`, `/vsicurl/`) | none | same as the raster reads | **Not reachable.** rasterio exposes no VSI _read_ primitive — `rasterio._vsiopener` and `rasterio._filepath` are the inverse direction (Python file-like → GDAL), `rasterio._path._vsi_path` is string munging only. Reaching `VSIFOpenL` means either the `osgeo.gdal` bindings (heavyweight, ABI-pinned to libgdal — this session hit exactly that failure) or a ctypes shim (fragile). |
| **`obstore`** | 1 (v0.11.0, Rust wheels, only `typing-extensions` on py<3.13) | own env vars, or a pluggable credential provider | Handles custom-endpoint S3, requester-pays, Azure and HTTP. Native auth is boto3-free but does not cover every mechanism GDAL does — see below. |
| **`boto3` / `s3fs`** | heavy tree | own config | Well-understood, but the largest dependency tree of the three. `fsspec` is already present transitively but `s3fs` is not, so fsspec alone does not help. |
| **`requests` / `httpx` only** | none / promote extra | n/a | **Insufficient** — covers only Planetary Computer. |

The choice therefore turns on capability and dependency weight, both of which favour
`obstore`.

A non-GDAL client reads its own environment variable names (`AWS_ENDPOINT_URL` rather
than GDAL's `AWS_S3_ENDPOINT`, and so on). That is a deployment-configuration detail, not
a design constraint: the Helm chart already exposes a free-form `env` map
(`deployment/k8s/charts/values.yaml`) and an `envVars.fromSecret[]` mechanism that maps
any secret key to any environment variable name, so one credential secret feeding two
variable names is a two-line values entry. Document the mapping alongside the existing
GDAL tuning variables and move on.

**Where the two clients genuinely diverge is credential _mechanisms_, not variable names.**
`obstore` has two auth paths — native (Rust) and Python credential providers — and the
native path covers a strictly smaller set than GDAL:

| Mechanism | GDAL | `obstore` native |
| --- | --- | --- |
| Static key/secret (+ session token) via env | yes | yes |
| Custom S3 endpoint, path-style addressing | yes | yes |
| Requester-pays | yes | yes |
| WebIdentity / IRSA (`AWS_WEB_IDENTITY_TOKEN_FILE` + `AWS_ROLE_ARN`) | yes | yes |
| EC2 IMDS / ECS container credentials | yes | yes |
| **`~/.aws/credentials` profiles, `AWS_PROFILE`, SSO** | **yes** | **no** |

`AWS_PROFILE` support was [deliberately removed upstream in `arrow-rs`][arrow-rs-4238],
so it is not coming back to the native path. The documented route is
`obstore.auth.boto3.Boto3CredentialProvider`, which pulls in **boto3**
([developmentseed/obstore#571][obstore-571]).

The practical shape of this is the opposite of what one might fear:

- **The reference deployment is unaffected.** CDSE authenticates with a static key/secret
  plus a custom endpoint (exactly what `.env.cdse` sets) — fully covered natively.
- **Production Kubernetes is unaffected.** IRSA and IMDS are both native.
- **Local development is where it bites.** A developer authenticating via `AWS_PROFILE` or
  SSO would find raster reads working (GDAL) and annotation fetches failing (obstore) —
  a confusing, asymmetric failure precisely in the environment where it is least expected.

**Mitigation:** ship `boto3` as an optional extra and select `Boto3CredentialProvider`
when `AWS_PROFILE` is set, falling back to native auth otherwise. That keeps the default
install boto3-free while making the developer path work. The `AssetFetcher` protocol below
is what makes this a five-line branch rather than a refactor.

**Bonus:** obstore ships a built-in `PlanetaryComputerCredentialProvider` (no extra
dependency) and a `NasaEarthdataCredentialProvider` (needs `requests`). The former would
replace the hand-rolled SAS-token minting in the Phase 0 prototype and makes Planetary
Computer support nearly free if it is ever declared supported; the latter matters if the
OPERA RTC path of Option A is pursued.

[arrow-rs-4238]: https://github.com/apache/arrow-rs/pull/4238
[obstore-571]: https://github.com/developmentseed/obstore/issues/571

**Recommendation:** define a narrow `AssetFetcher` protocol (`fetch(href, item) -> bytes`)
so the mechanism is swappable and testable, and ship `obstore` as the default
implementation, with `boto3` as an optional extra for profile-based credentials.

**CDSE-only fallback worth noting:** CDSE preserves the original SAFE layout on S3
(hrefs literally contain `…​.SAFE/annotation/calibration/…`), so GDAL's
`SENTINEL1_CALIB:` subdataset path would likely work _there_ and let GDAL fetch and apply
the LUTs itself with the already-configured credentials. It still performs no thermal
noise removal and does not generalise to the other catalogues (§1.6f), so it is a
fallback, not the design.

### 7.7 Dependencies

rasterio (GCPs, TPS transformer, reproject) and numpy — both already present. Plus:

- **`obstore`** — the fetcher, per §7.6. Required.
- **`boto3`** — optional extra, only for `AWS_PROFILE`/SSO credentials (§7.6).
- **`defusedxml`** — required, not optional: the stdlib `xml.etree.ElementTree` parser on
  this project's Python 3.13.1 was measured **expanding** a billion-laughs payload
  (§9.2). Alternatively disable entity resolution explicitly and keep the stdlib parser.

### 7.8 Acceptance criteria

1. **Plausibility, not cross-validation.** External comparison against SNAP is
   **deferred** — it needs a SNAP install and a documented generation procedure, and it
   would gate Phase 1 on work outside this repo. Instead assert that
   `sigma0-ellipsoid` over a known open-water fixture falls in a physically sensible band
   (VV open water at ~38° incidence sits around −20 to −25 dB; the Phase 0 run against
   CDSE measured a median of **−23.0 dB**, §1.6h). This catches gross errors — wrong LUT,
   missing square, K applied twice — without an external toolchain. Promote to a proper
   SNAP golden test as a follow-up once someone has the reference to hand.
2. `gamma0-ellipsoid / sigma0-ellipsoid == 1/cos(θ_ell)` to **< 0.01 dB** (self-consistency,
   §1.6d). With criterion 1 deferred, this and criterion 3 carry most of the radiometric
   assurance — they are exact identities derived from ESA's own LUT definitions, so they
   are strong, just not independent of ESA's annotations being correct.
3. `beta0` is spatially constant × DN² to floating-point precision.
4. Geolocation: round-tripping the GCPs through the inverse map gives **< 1 m** RMS
   residual (i.e. TPS really is in use). This is the test that would have caught both the
   order-2 default and the ignored `METHOD=GCP_TPS` — assert the residual, never that a
   particular option was passed.
5. `noise_removal=true` vs `false` differ measurably in a low-backscatter (open water,
   cross-pol) region and are identical to within noise elsewhere.
6. A 256 × 256 web-mercator tile at zoom 10 returns in **< 3 s** warm-cache.
7. Every unsupported parameter raises the documented error, with a test each.

### 7.9 Test plan

- **Unit:** LUT bilinear interpolation against hand-computed values; noise azimuth block
  assembly; incidence-angle identity; coefficient maths; error taxonomy.
- **Fixture:** commit a small subset (one burst-sized window + trimmed annotation XML) so
  the suite runs offline, consistent with the existing `tests/fixtures/` approach.
- **Golden:** deferred with criterion 1. When it lands, it is one reference tile produced
  by SNAP (or pyroSAR), stored as a compressed array, with its generation procedure
  documented. Until then the fixture test asserts a physically sensible dB band, not an
  external reference.
- **Regression:** criterion 4, to pin the TPS finding.
- **Catalogue contract:** a table-driven test over committed item JSON from each
  catalogue, asserting the §1.7 requirements — annotation siblings resolvable, GCPs
  present, and that a misleading `proj:*` is ignored rather than used. Adding a catalogue
  means adding a row and a fixture, which is what makes portability a verified claim
  rather than an assumption. The fabricated `proj:transform` published by **both** Earth
  Search and Planetary Computer is the natural first negative case; Planetary Computer's
  is the cleaner one to assert against, being bbox-derived to the last digit.

---

## 8. Documentation deliverables

- `docs/src/sar-backscatter.md` — supported coefficients, the accuracy statement from §5
  verbatim, worked process-graph examples, and the pointer to OPERA RTC-S1 for CARD4L needs.
- Add to `docs/mkdocs.yml` nav under **Architecture → Special Features**.
- Register `docs/adr/` in the nav (or state explicitly that ADRs are repo-only, matching
  `docs/audits/`).

---

## 9. Open questions

Six of the original seven are now settled. Struck items are kept for the audit trail.

1. ~~**Annotation asset fetcher.**~~ **Settled — verified against CDSE end to end**
   (§1.6h). `obstore` 0.11.0 + `Boto3CredentialProvider` fetches the annotation XML from
   `s3://eodata` with `AWS_PROFILE=cdse` and a custom endpoint, alongside GDAL reading the
   measurement TIFF with the same profile. Three API details found the hard way:
   `S3Store` is a Rust extension so its signature is opaque to `inspect`; it **rejects
   `endpoint=None`** (omit the option rather than passing `None`); and
   `Boto3CredentialProvider` takes a **`session`**, not a `profile_name` — pass
   `boto3.Session(profile_name=…)`, or rely on boto3's own `AWS_PROFILE` handling.
   §7.6 records the credential-mechanism gap this exposes: obstore's native auth covers
   static keys, IRSA, IMDS and ECS but **not `AWS_PROFILE`/SSO**, so `boto3` is a required
   optional extra for profile-based deployments — including, in practice, CDSE as
   configured on this machine.
2. ~~**XML hardening — "cheap insurance".**~~ **Settled, and it is not optional.** Measured
   on this project's Python 3.13.1: a billion-laughs payload through
   `xml.etree.ElementTree` **expands** (30 000 characters from a 4-level bomb). The stdlib
   parser is genuinely vulnerable. Use `defusedxml`, or disable entity resolution
   explicitly. Low likelihood given a trusted catalogue, but it is a real exposure rather
   than a theoretical one.
3. ~~**`METHOD=GCP_TPS` transformer plumbing.**~~ **Settled** by the Phase 0 prototype:
   `METHOD` is ignored by both `WarpedVRT` and `reproject`; neither can produce TPS. The
   design no longer delegates the transformer to GDAL at all (§1.6b, §7.3).
4. ~~**`absoluteCalibrationConstant`.**~~ **Settled — and the correct action is to do
   nothing.** ESA's calibration technical note ([ESA-EOPG-CSCOP-TN-0002][esa-cal]) states
   plainly: _"the calibration constant K is built-in the LUTs and is just provided for
   completeness."_ The document's own equations confirm it —
   `A_σ = √(A_dn²·K / sin α)` — so `σ⁰ = DN²/A_σ²` is complete as written. **Applying K
   separately would introduce the error, not remove it.** This matters: K is not always 1
   — measured `1.000` (S1A), **`1.393` (S1B)**, **`0.8995` (S1C)** — so a well-intentioned
   "apply the constant" would put S1B out by +1.44 dB and S1C by −0.46 dB, and only on
   some missions.
5. ~~**Which collections to accept.**~~ **Settled by enumeration.** Sampling 200 CDSE items
   in each of 2015/2017/2019/2021/2023/2025/2026 yields exactly:
   `IW_GRDH_1S`, `IW_GRDH_1S_B`, `IW_GRDH_1S_C`, `EW_GRDM_1S`, `EW_GRDM_1S_B`,
   `EW_GRDM_1S_C`, `EW_GRDH_1S`, `EW_GRDH_1S_B`, plus stripmap `S2_/S3_/S4_/S6_GRDH_1S`
   (and `S3_GRDH_1S_B`). The trailing `_B`/`_C` is the **platform** (S1A has no suffix),
   not an IPF version — which is why the geopyspark driver's three-entry whitelist works.
   A pattern such as `^(IW|EW|S[1-6])_GRD[HM]_1S(_[A-Z])?$` covers the observed CDSE
   archive. **But do not gate on it.** Per §1.7, `product:type` is absent on Earth Search
   and Planetary Computer, which publish `sar:product_type = "GRD"` instead — a whitelist
   of full identifiers would reject them outright. Gate on capability (do the annotation
   siblings resolve?) and use product type only to sharpen errors and to reject
   positively-unsupported types such as `SLC`.
6. **Speckle filtering — deferred, not open.** Sentinel Hub exposes a Lee filter; openEO
   has no standard parameter, so it would live in `options` and reduce portability. Out of
   scope for Phase 1; recorded as a follow-up.
7. ~~**Border noise removal.**~~ **Reframed and mostly settled** — the real hazard turned
   out not to be border noise but the **noise annotation schema change at IPF 2.90**
   (§1.6g), which breaks parsing outright on everything before March 2018. That is now a
   Phase 1 requirement rather than a question. What remains genuinely open is the narrow
   product decision: whether pre-2018 acquisitions are in scope at all, and if so whether
   their residual border-noise artefacts warrant the Ali et al. algorithm. Ship the
   two-schema parser either way; treat explicit border-noise removal as a follow-up driven
   by a real user need.

---

## 10. References

- openEO process specs:
  [`sar_backscatter`](https://github.com/Open-EO/openeo-processes/blob/master/proposals/sar_backscatter.json),
  [`ard_normalized_radar_backscatter`](https://github.com/Open-EO/openeo-processes/blob/master/proposals/ard_normalized_radar_backscatter.json)
- [openEO ARD / SAR backscatter use case](https://docs.openeo.cloud/usecases/ard/sar/#backscatter-computation)
- [openeo-geopyspark-driver `s1backscatter_orfeo.py`](https://github.com/Open-EO/openeo-geopyspark-driver/blob/master/openeogeotrellis/collections/s1backscatter_orfeo.py)
- [Sentinel Hub Sentinel-1 GRD processing options](https://docs.sentinel-hub.com/api/latest/data/sentinel-1-grd/#processing-options)
- [CDSE openEO processing documentation](https://documentation.dataspace.copernicus.eu/APIs/openEO/openeo_processing.html)
- [CEOS CARD4L NRB PFS v5.5](https://ceos.org/ard/files/PFS/NRB/v5.5/CARD4L-PFS_NRB_v5.5.pdf)
- Small, D. (2011), _Flattening Gamma: Radiometric Terrain Correction for SAR Imagery_, IEEE TGRS
- [`sarsen`](https://github.com/bopen/sarsen) and [`xarray-sentinel`](https://github.com/bopen/xarray-sentinel) (B-Open, Apache-2.0)
- [OPERA RTC-S1 product guide](https://hyp3-docs.asf.alaska.edu/guides/opera_rtc_product_guide/)
- [GDAL SAFE driver](https://gdal.org/en/stable/drivers/raster/safe.html)
