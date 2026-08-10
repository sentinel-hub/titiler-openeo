# Sentinel-2 View/Sun Angle Bands

Sentinel-2 L2A view and sun geometry — `viewZenithMean`, `viewAzimuthMean`,
`sunZenithAngles`, `sunAzimuthAngles`, the band names SentinelHub-style eval
scripts use — are ordinary cube bands. `load_collection` can request any of
them directly, the same way [Sentinel-1's calibration and noise
bands](sar-backscatter.md#calibration-and-noise-bands) can.

## Where the values come from

None of this data is virtual: it comes from `MTD_TL.xml`, the per-granule
tile metadata file every Sentinel-2 product ships alongside its raster
bands, published as a STAC asset (`granule_metadata` on CDSE and Earth
Search, `granule-metadata` on Planetary Computer).

| Band | Source | Shape |
| --- | --- | --- |
| `viewZenithMean` | Mean, across all 13 Sentinel-2 bands, of `Mean_Viewing_Incidence_Angle_List`'s per-band `ZENITH_ANGLE` | scalar, uniform across the scene |
| `viewAzimuthMean` | Circular mean, across all 13 bands, of the same list's `AZIMUTH_ANGLE` | scalar, uniform across the scene |
| `sunZenithAngles` | `Sun_Angles_Grid/Zenith`, a real 23×23 grid at 5000 m spacing | per-pixel, bilinearly interpolated |
| `sunAzimuthAngles` | `Sun_Angles_Grid/Azimuth` | per-pixel, bilinearly interpolated |

The "Mean" bands are genuinely scalar — that's what the source data itself
is (one measurement per band, not per pixel), matching their name. The sun
angle bands are genuinely spatially resolved: sun position varies smoothly
but measurably across a 110 km tile, and the real grid captures that
instead of a flat approximation.

## Supported collections

`sentinel-2-l2a` is served from CDSE, Earth Search and Planetary Computer.
Any STAC catalogue configured for this backend works the same way, as long
as its items expose a `granule_metadata` or `granule-metadata` asset
(`application/xml`, role `metadata`) pointing at the item's `MTD_TL.xml`.

**Planetary Computer is not currently supported for reading these bands.**
Discovery still advertises them (`/collections/sentinel-2-l2a` lists all
four, honestly, since the STAC contract doesn't distinguish "advertised"
from "readable"), but a live, unauthenticated fetch of PC's
`granule-metadata` asset returns `HTTP 409 PublicAccessNotPermitted` —
requesting one of these bands against a PC-backed deployment fails with a
clear fetch error, not a wrong value. See
[ADR 0004 §3.1](https://github.com/sentinel-hub/titiler-openeo/blob/main/docs/adr/0004-sentinel2-view-sun-angle-bands.md)
for the evidence and status.

## Worked example

Sun zenith angle alongside the red band, for a scene-level illumination
check:

```json
{
  "process_graph": {
    "load1": {
      "process_id": "load_collection",
      "arguments": {
        "id": "sentinel-2-l2a",
        "spatial_extent": {"west": -1.5, "south": 62.0, "east": -1.0, "north": 62.3},
        "temporal_extent": ["2026-08-07T00:00:00Z", "2026-08-08T00:00:00Z"],
        "bands": ["B04_10m", "sunZenithAngles"]
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

Band names for the raster bands follow each catalogue's own convention
(`B04_10m` on CDSE, `red` on Earth Search, `B04` on Planetary Computer) —
the four angle band names above are the same across all three.

## Design reference

The full evidence, mechanism and open limitations are documented in
[ADR 0004](https://github.com/sentinel-hub/titiler-openeo/blob/main/docs/adr/0004-sentinel2-view-sun-angle-bands.md),
which extends the band-sources mechanism
[ADR 0002](https://github.com/sentinel-hub/titiler-openeo/blob/main/docs/adr/0002-band-sources.md)
built for Sentinel-1. ADRs are repo-only documentation (like `docs/audits/`)
and are not published on this site — follow the links on GitHub.
