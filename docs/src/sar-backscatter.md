# SAR Backscatter (Sentinel-1)

`sar_backscatter` calibrates Sentinel-1 GRD digital numbers (DN) into physical radar
backscatter. This is **Phase 1**: ellipsoid-referenced coefficients only. Terrain
correction (DEM-based geometric orthorectification, radiometric terrain flattening) is
not implemented and is tracked as later phases — see the
[design reference](#design-reference) below for the full rationale and what is planned.

## Supported collections

`sentinel-1-grd` is served from CDSE, Earth Search and Planetary Computer. Any STAC
catalogue configured for this backend works the same way, as long as its items expose
the same asset shape those three do: one measurement asset per polarisation (`vv`,
`vh`, `hh`, `hv`) plus its `schema-calibration-<pol>` and `schema-noise-<pol>` sibling
annotation assets.

Acceptance is **capability-gated, not identity-gated**: `sar_backscatter` does not
require a specific `product:type` or `sar:product_type` value (these are inconsistent
across catalogues), only that the assets it needs actually resolve. It does reject
`sar:product_type` values positively known to be unsupported (`SLC`, `OCN`) — the
process assumes detected GRD amplitude data.

Calibration happens per source item, before mosaicking, so a spatial extent whose
acquisition datetime spans more than one item (e.g. adjacent orbit swaths captured at
the same pass) calibrates correctly — each item's own calibration/noise LUTs are used
for its own pixels, never blended with another item's.

## Supported parameters

| Parameter | Phase 1 support |
| --- | --- |
| `coefficient` | `beta0`, `sigma0-ellipsoid`, `gamma0-ellipsoid`, `null`. **Not** `sigma0-terrain` or `gamma0-terrain` (the openEO spec's own default) — callers must opt into an ellipsoid coefficient explicitly. |
| `elevation_model` | Must be `null`. No DEM is used. |
| `mask` | Supported. Adds a `mask` band: `1.0` valid, `0.0` invalid/no-data. |
| `noise_removal` | Supported (default `true`). Subtracts ESA's thermal noise LUT before calibration; negative results are clamped to `0` and counted. |
| `ellipsoid_incidence_angle` | Supported. Adds an `ellipsoid_incidence_angle` band, in degrees, recovered from the calibration LUTs alone (no orbit geometry needed). |
| `contributing_area` | **Not supported** — requires a DEM. |
| `local_incidence_angle` | **Not supported** — requires a DEM. |

Every unsupported parameter raises a clear error rather than silently approximating:

| Condition | Error |
| --- | --- |
| `coefficient` is `sigma0-terrain` or `gamma0-terrain` | `ProcessParameterInvalid` |
| `contributing_area=true` or `local_incidence_angle=true` | `FeatureUnsupported` |
| `elevation_model` is not `null` | `DigitalElevationModelInvalid` |
| `data` is not a STAC-item-backed `load_collection`/`load_stac` stack | `ProcessParameterInvalid` |
| An expected calibration/noise band (e.g. `vv_sigma0_lut`) is missing from `data` | `ProcessParameterInvalid`, naming the missing band |
| `sar:product_type` is positively `SLC` or `OCN` | `ProcessParameterInvalid` |

## Scientific accuracy statement

Reproduced verbatim from the design ADR (§5), which governs Phase 1's radiometric and
geometric accuracy claims:

> **Radiometric accuracy — excellent.** The computation
> `σ⁰ = (DN² − η) / A_σ²` is exactly the relation ESA specifies
> ([ESA-EOPG-CSCOP-TN-0002](https://sentinels.copernicus.eu/documents/247904/685163/S1-Radiometric-Calibration-V1.0.pdf) §4), with the same supplied LUTs, and is what SNAP's
> Calibration operator implements. The absolute calibration constant K is already folded
> into the LUTs and must **not** be applied again. Agreement with SNAP is expected to
> be limited only by resampling choice, well inside Sentinel-1's ~0.3–0.5 dB absolute
> radiometric accuracy. Decimation bias is ≤ 0.06 dB, and ESA explicitly endorses
> averaging in power — *"the average backscatter coefficient over an area of interest can be
> used instead: σ⁰ = ⟨DN²⟩ / A_σ²"*.
>
> **A nuance on "ellipsoid".** The Earth model behind the LUTs is not the bare WGS84
> ellipsoid: ESA defines it as *"the ellipsoid inflated with an average height"* for the
> scene. So `sigma0-ellipsoid` is already referenced to a mean scene elevation, which
> slightly reduces — but does not remove — the terrain error below. The openEO coefficient
> name `sigma0-ellipsoid` is still the correct label; these docs should just not overstate it
> as "sea-level referenced".
>
> **Geometric accuracy — terrain-dependent, but less so than first stated.** ESA computes
> the geolocation grid against a terrain model — sampled 10 CDSE products across
> 2015–2026 show GCP height is 0 m over ocean and sea ice, but 196–253 m and 251–351 m
> over European land at 52–56°N, and 0–835 m over the tropics at 4–7°N. Geocoding
> through these GCPs therefore already accounts for terrain **at grid resolution** — a
> coarse ~10–25 km sampling. What remains uncorrected is local relief *relative to the
> smoothly interpolated grid*, not the full terrain height. The figures below are
> consequently an **upper bound**, pessimistic for large-scale topography while
> remaining valid for local relief.
>
> This does not change the radiometry: `sigma0-ellipsoid`/`gamma0-ellipsoid` use the
> ellipsoid incidence angle from the calibration LUTs regardless of what the
> geolocation grid does. The geometric and radiometric senses of "ellipsoid" are
> independent.
>
> Planimetric error, as an upper bound:
>
> ```
> Δx  =  Δh / tan(θ)
> ```
>
> For IW (θ ≈ 29°–46°) that is **1.0× to 1.8× the height above the grid's own
> reference** — which is the terrain-sampled grid rather than the bare ellipsoid.
> Concretely: 100 m of relief → 100–180 m of horizontal displacement; 500 m →
> 0.5–0.9 km. Over flat coastal or agricultural terrain the error is metres. Over
> mountains it is catastrophic for any per-pixel analysis.
>
> **Radiometric terrain effects — uncorrected.** On sloping terrain the illuminated area
> per pixel departs from the ellipsoid assumption; `gamma0-ellipsoid` typically deviates
> from `gamma0-terrain` by ±3 dB on moderate slopes and >6 dB on steep foreslopes. Layover
> and shadow are neither detected nor masked.
>
> **Fit for purpose:** open water and flood mapping, agriculture, wetlands, sea ice, oil
> spill and ship detection, urban change over low-relief terrain, and any application that
> compares same-geometry acquisitions (same relative orbit) where the terrain bias largely
> cancels.
>
> **Not fit for purpose:** mountainous terrain, cross-orbit (ascending vs descending)
> comparison, biomass or soil-moisture retrieval, anything requiring CARD4L compliance.

## Worked examples

Ellipsoid sigma0, with a validity mask, over VV/VH:

```json
{
  "process_graph": {
    "load1": {
      "process_id": "load_collection",
      "arguments": {
        "id": "sentinel-1-grd",
        "spatial_extent": {"west": 139.5, "south": 35.2, "east": 140.2, "north": 35.8},
        "temporal_extent": ["2026-07-08T20:42:00Z", "2026-07-08T20:44:00Z"],
        "bands": ["vv", "vh"]
      }
    },
    "sar1": {
      "process_id": "sar_backscatter",
      "arguments": {
        "data": {"from_node": "load1"},
        "coefficient": "sigma0-ellipsoid",
        "noise_removal": true,
        "mask": true
      }
    },
    "save1": {
      "process_id": "save_result",
      "arguments": {
        "data": {"from_node": "sar1"},
        "format": "GTiff"
      },
      "result": true
    }
  }
}
```

Gamma0, with the ellipsoid incidence angle also requested (e.g. to build a
polarimetric or terrain-aware downstream index):

```json
{
  "process_graph": {
    "load1": {
      "process_id": "load_collection",
      "arguments": {
        "id": "sentinel-1-grd",
        "spatial_extent": {"west": 139.5, "south": 35.2, "east": 140.2, "north": 35.8},
        "temporal_extent": ["2026-07-08T20:42:00Z", "2026-07-08T20:44:00Z"],
        "bands": ["vv", "vh"]
      }
    },
    "sar1": {
      "process_id": "sar_backscatter",
      "arguments": {
        "data": {"from_node": "load1"},
        "coefficient": "gamma0-ellipsoid",
        "ellipsoid_incidence_angle": true
      }
    },
    "save1": {
      "process_id": "save_result",
      "arguments": {
        "data": {"from_node": "sar1"},
        "format": "GTiff"
      },
      "result": true
    }
  }
}
```

See the [Sentinel-1 SAR Backscatter RGB Composite](notebooks/sar_backscatter_rgb.ipynb)
notebook for an end-to-end run producing a VH/VV/VH:VV false-colour composite, a useful
visual sanity check of a real calibrated product (water dark, vegetation reddish, urban
bright).

## Calibration and noise bands

`sar_backscatter` calibrates by reading the calibration constant and thermal noise
value from six bands per polarisation, derived directly from each item's own
calibration/noise annotation XML. These are ordinary cube bands — `load_collection`
can request any of them directly, independent of `sar_backscatter`, for building a
coefficient this process does not itself provide.

| Band | Description |
| --- | --- |
| `<pol>_sigma0_lut` | Sigma0 (`σ⁰`) calibration constant `A` |
| `<pol>_beta0_lut` | Beta0 (`β⁰`) calibration constant `A` |
| `<pol>_gamma0_lut` | Gamma0 (`γ⁰`) calibration constant `A` |
| `<pol>_dn_lut` | Raw-DN calibration constant (rarely needed directly — the other three already have the absolute calibration constant folded in, per the accuracy statement above) |
| `<pol>_ellipsoid_incidence_angle` | Ellipsoid incidence angle in degrees, recovered from the sigma0/gamma0 LUTs alone |
| `<pol>_noise_lut` | Thermal noise, in DN² |

`<pol>` is the polarisation code (`vv`, `vh`, `hh`, `hv`). Calling `sar_backscatter`
with a given `coefficient` requests the matching LUT band (and the noise band, if
`noise_removal` is true) on your behalf automatically — you only need to request one of
these bands yourself if you want its value directly, e.g. to inspect the calibration
constant independent of any DN:

```json
{
  "process_graph": {
    "load1": {
      "process_id": "load_collection",
      "arguments": {
        "id": "sentinel-1-grd",
        "spatial_extent": {"west": 139.5, "south": 35.2, "east": 140.2, "north": 35.8},
        "temporal_extent": ["2026-07-08T20:42:00Z", "2026-07-08T20:44:00Z"],
        "bands": ["vv", "vv_sigma0_lut", "vv_ellipsoid_incidence_angle"]
      }
    },
    "save1": {
      "process_id": "save_result",
      "arguments": {
        "data": {"from_node": "load1"},
        "format": "GTiff"
      },
      "result": true
    }
  }
}
```

## Need CARD4L or terrain-corrected gamma0?

This backend does not produce terrain-corrected (`gamma0-terrain`/`sigma0-terrain`)
or CARD4L-compliant output. If you need that today, use a pre-computed Radiometric
Terrain Correction (RTC) collection instead — for example
[OPERA RTC-S1](https://hyp3-docs.asf.alaska.edu/guides/opera_rtc_product_guide/), which
ships CARD4L-compliant terrain-flattened gamma0 directly as a STAC collection you can
`load_collection` like any other. `sar_backscatter`'s terrain correction (Phase 2:
geometric orthorectification, then Phase 3: radiometric terrain flattening and
registering `ard_normalized_radar_backscatter`) is on the roadmap but not built yet.

## Design reference

The full design rationale, empirical findings, and phased implementation plan live in
[ADR 0001](https://github.com/sentinel-hub/titiler-openeo/blob/main/docs/adr/0001-sar-backscatter.md).
The calibration/noise bands documented above — how they are discovered, read per item
before mosaicking, and how `sar_backscatter` consumes them — are
[ADR 0002](https://github.com/sentinel-hub/titiler-openeo/blob/main/docs/adr/0002-band-sources.md)'s
subject. ADRs are repo-only documentation (like `docs/audits/`) and are not published
on this site — follow the links on GitHub.
