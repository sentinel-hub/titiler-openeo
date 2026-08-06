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
`tests/test_openeo_reader.py`.

**Polar on purpose.** rio-tiler collapses a GCP grid to one affine, and that
approximation's error is strongly latitude-dependent — measured on real
products, the affine and `MAX_GCP_ORDER=3` warp paths diverge by ≤ 30 m at
69° N but 204–2042 m at 81–86° N, because meridian convergence makes a single
affine a poor model of the grid. A mid-latitude grid cannot discriminate the
two paths at all: the difference falls below the resampling quantisation
floor. See `docs/adr/0001-sar-backscatter.md` §1.6i and issue #343.

## STAC item fixtures (`items/`)

Trimmed real STAC items — one per catalogue this project targets — fetched
live 2026-07-30, used by `tests/test_sar_catalogue_contract.py` (ADR §7.9).
Each keeps only `sar:*`/`proj:*`/`product:type` properties and the
measurement + `schema-calibration-*`/`schema-noise-*` assets; thumbnails,
manifests and other unrelated assets are dropped.

| File | Source item | Notes |
| --- | --- | --- |
| `cdse.json` | `S1D_IW_GRDH_1SDV_20260730T185910_20260730T185937_003907_00710E_0863_COG` | No `proj:*` at all. `sar:product_type` absent (only `product:type`, a different field). |
| `earth_search.json` | `S1D_IW_GRDH_1SDV_20260730T185910_20260730T185937_003907_00710E` | Same acquisition as the CDSE fixture, from a different catalogue. Publishes a bbox-derived `proj:transform` that is fiction for SAR geometry. |
| `planetary_computer.json` | `S1C_EW_GRDM_1SDH_20260730T181143_20260730T181243_008776_01164E` | HH/HV (EW mode), not VV/VH — exercises a different polarisation pair. Also publishes a bbox-derived `proj:transform`. |

Adding a catalogue means adding a row here and a fixture, per the ADR — this
is what makes portability a verified claim rather than an assumption.

## STAC collection fixtures (`collections/`)

Trimmed real STAC collections — one per catalogue, `item_assets` only — fetched
live 2026-08-06 against each catalogue's `/collections/sentinel-1-grd`, used by
`tests/test_band_sources_discovery.py` (docs/adr/0002-band-sources.md §1.2).
Unlike the item fixtures above, these describe the *collection's* declared
asset shape (media type, roles), not one item's actual hrefs.

| File | Notable difference from the others |
| --- | --- |
| `cdse.json` | Only catalogue with a `Product` asset (`application/zip`, roles include `data`) — the fixture that pins the `Product`-as-band fix. Manifest key is `safe_manifest` (underscore). |
| `earth_search.json` | No `Product` asset. Manifest key is `safe-manifest` (hyphen). |
| `planetary_computer.json` | Same shape as Earth Search. |

All three publish `schema-calibration-<pol>`/`schema-noise-<pol>`/
`schema-product-<pol>` as `application/xml` with role `metadata`, and all
three currently use the collection id `sentinel-1-grd` — verified live, not
assumed.
