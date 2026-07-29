# Sentinel-1 annotation fixtures

Real ESA calibration/noise annotation XML from CDSE, trimmed to 4 calibration
vectors x 16 range samples (and, for the modern noise schema, 8 samples per
azimuth block) so the parser is exercised against genuine ESA output rather
than synthetic data, while keeping the files small enough to commit.

| File | Source item | Schema |
| --- | --- | --- |
| `calibration_ipf290.xml`, `noise_ipf290.xml` | `S1C_IW_GRDH_1SDV_20260728T151018_20260728T151051_008745_01154E_1CF9_COG` | IPF >= 2.90: `noiseRangeVectorList` + `noiseAzimuthVectorList` |
| `calibration_legacy.xml`, `noise_legacy.xml` | `S1A_IW_GRDH_1SDV_20160601T234802_20160601T234827_011524_01195C_5393_COG` | IPF < 2.90: single `noiseVectorList`, no azimuth block |

See `docs/adr/0001-sar-backscatter.md` S1.6g for why both schemas must be
covered: a parser written against only the modern layout fails on every CDSE
product before March 2018.

## GCP geolocation grid

`gcps_ew_grdm_polar.json` — the 483-point geolocation grid from
`S1C_EW_GRDM_1SDH_20260728T084043_20260728T084148_008741_01152A`
(EW GRDM, HH, 10725 × 10777, **81.1–86.6° N**), used by
`tests/test_gcp_reader.py`.

**Polar on purpose.** rio-tiler collapses a GCP grid to one affine, and that
approximation's error is strongly latitude-dependent — measured on real
products, the affine and `MAX_GCP_ORDER=3` warp paths diverge by ≤ 30 m at
69° N but 204–2042 m at 81–86° N, because meridian convergence makes a single
affine a poor model of the grid. A mid-latitude grid cannot discriminate the
two paths at all: the difference falls below the resampling quantisation
floor. See `docs/adr/0001-sar-backscatter.md` §1.6i and issue #343.
