# Sentinel-2 view/sun angle band fixtures

See `docs/adr/0004-sentinel2-view-sun-angle-bands.md`.

## `mtd_tl_sample.xml`

A real ESA `MTD_TL.xml` (item `S2B_29VPG_20260807_0_L2A`, Earth Search's
`granule_metadata.xml` copy, fetched live 2026-08-07), trimmed to keep
`Tile_Geocoding`, `Tile_Angles/Sun_Angles_Grid` (both `Zenith` and `Azimuth`,
the full 23×23 grid intact), `Tile_Angles/Mean_Sun_Angle` and
`Tile_Angles/Mean_Viewing_Incidence_Angle_List` (all 13 band entries
intact) -- everything the parser actually reads. Dropped:
`Tile_Angles/Viewing_Incidence_Angles_Grids` (26 elements, the per-band,
per-detector spatial grids -- not used by this feature, see ADR 0004 §1.3)
and `Quality_Indicators_Info` (unrelated). 178 KB -> 15 KB. The grid values
and the 13-entry list are kept **byte-for-byte real**, not synthetic --
`tests/test_sentinel2_tile_metadata.py`'s oracle assertions depend on exact
values (`mean_view_zenith == 8.396813497980254`, matching this item's own
`view:incidence_angle` STAC property to full float precision; ADR 0004
§1.3 explains why).

## `collections/{cdse,earth_search,planetary_computer}.json`

Trimmed real STAC collections — one per catalogue, `item_assets` only —
fetched live 2026-08-07 against each catalogue's
`/collections/sentinel-2-l2a`, used by
`tests/test_sentinel2_band_sources_discovery.py`. Kept: three 10 m raster
bands (red/blue/nir-equivalent, to exercise `pick_nominal_sibling_by_resolution`),
the granule-metadata asset, a manifest/tile-info asset, and (CDSE only) the
`Product` archive asset — CDSE is the only catalogue that ships one for
Sentinel-2, same as it does for Sentinel-1 (`tests/fixtures/sar/README.md`).

| File | Asset key spelling | Notable difference |
| --- | --- | --- |
| `cdse.json` | `B02_10m`/`B04_10m`/`B08_10m`, `granule_metadata` (underscore) | `image/jp2`, not COG GeoTIFF. Has a `Product` (`application/zip`) asset. |
| `earth_search.json` | `blue`/`red`/`nir`, `granule_metadata` (underscore) | COG GeoTIFF (`image/tiff; ...`). |
| `planetary_computer.json` | `B02`/`B04`/`B08`, `granule-metadata` (**hyphen**) | COG GeoTIFF. The hyphenated metadata asset key is why `_GRANULE_METADATA_ASSET` in `sentinel2_sources.py` is `granule[_-]metadata`, not a literal string. |

All three currently use the collection id `sentinel-2-l2a` — verified live,
not assumed.

## `items/{cdse,earth_search,planetary_computer}.json`

Trimmed real STAC items — one per catalogue, fetched live 2026-08-07 — used
by `tests/test_sentinel2_catalogue_contract.py`. Kept: the same three
raster band assets as the collection fixtures (with real hrefs) plus the
granule-metadata asset, and only the properties this feature cares about.

| File | Source item | Notes |
| --- | --- | --- |
| `cdse.json` | `S2B_MSIL2A_20260807T114349_N0512_R123_T31VCK_20260807T141559` | Has `view:*` properties (unused by production code — kept only as documentation that CDSE publishes them too, ADR 0004 §1.2). `granule_metadata` href is `s3://eodata/...MTD_TL.xml`, same auth shape as SAR's own annotation XML. |
| `earth_search.json` | `S2B_29VPG_20260807_0_L2A` | **The same item `mtd_tl_sample.xml` was downloaded from** — its `granule_metadata` href is real and publicly fetchable (no auth), the only one of the three that a live integration test could actually read end to end. |
| `planetary_computer.json` | `S2B_MSIL2A_20260807T081609_R121_T48XVR_20260807T101845` | No `view:*` properties at all (only `s2:mean_solar_zenith`/`s2:mean_solar_azimuth`, unused by this feature since it reads the granule-metadata asset directly, not flattened properties). Its `granule-metadata` href returns `HTTP 409 PublicAccessNotPermitted` on an anonymous fetch — confirmed live, ADR 0004 §1.3/§3.1. The catalogue-contract test asserts `resolve_band` resolves correctly against this fixture's *shape*; it does not claim the asset is fetchable. |

Adding a catalogue means adding a row here and a fixture, per the ADR — this
is what makes portability a verified claim rather than an assumption.
