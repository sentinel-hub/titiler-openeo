# ADR 0010 — Collection band summaries mirror `cube:dimensions`

- **Status:** Accepted
- **Date:** 2026-10-12
- **Deciders:** @emmanuelmathot
- **Related:** [#440](https://github.com/sentinel-hub/titiler-openeo/issues/440), [developmentseed/openeo-studio#103](https://github.com/developmentseed/openeo-studio/pull/103), [ADR 0002](0002-band-sources.md), [ADR 0007](0007-multiband-assets.md)

---

## 1. Context

A collection has three lists of bands:

- `cube:dimensions`: the band dimension. `getdimensions` makes it from the
  data assets, the bands of multi-band assets (ADR 0007) and the derived
  bands (ADR 0002). Some catalogues publish it upstream.
- `summaries.bands` (STAC 1.1). openEO Studio and the openEO Web Editor use
  it for their band picker. They use `cube:dimensions` only when it is empty.
- `summaries.eo:bands` (eo extension v1.x). The openEO Python client reads
  it first, and compares it with `cube:dimensions`.

Before this ADR, the three lists were different. On Planetary Computer,
`sentinel-2-l2a` had 22 bands in `cube:dimensions`, 18 entries in
`summaries.bands` and 13 in `summaries.eo:bands`:

- `summaries.eo:bands` was a copy of the upstream catalogue.
- `summaries.bands` came from `_add_band_summaries`. This function kept only
  the assets with `eo:bands`, so `SCL` was not in the list. The `visual`
  asset also gave duplicates (`B04`, `B03`, `B02`). The function was a
  workaround for an old openEO Studio, which is now fixed
  (developmentseed/openeo-studio#103).

The result: the band pickers did not show `SCL`, and the Python client
showed a warning for each `load_collection`.

openEO API 1.3.0 makes `cube:dimensions` required. The band dimension gives
the band names. The band fields in `summaries` describe the same bands, in
the same order.

## 2. Decision

`cube:dimensions` is the single source of truth for the bands of a
collection. `stacApiBackend._sync_band_summaries` runs after
`cube:dimensions` exists, for each collection, also when `cube:dimensions`
comes from upstream. It replaces `summaries.bands` and
`summaries.eo:bands` with one entry for each band-dimension value: the same
names, in the same order.

Each entry gets the metadata that exists for the band. The precedence is the
same as the read path (`SimpleSTACReader._get_asset_info`):

1. An `item_assets` key: the asset title or description, the asset `gsd`, and
   the spectral fields (common name, center wavelength, FWHM) of its band if
   the asset has exactly one band.
2. A band in a multi-band asset: the metadata of that band. The description
   names the asset and its title. If the band name or its STAC `name` is also
   an asset key, the description says that the band is not that asset.
3. A derived band: the `description` of its `BandSource`.

The upstream `summaries.eo:bands` and `summaries.bands` give missing fields,
by band name. They do not change the list.

The two summaries use different field names:

| `summaries.bands` (STAC 1.1) | `summaries.eo:bands` (eo v1.x) |
| --- | --- |
| `name` | `name` |
| `description` | `description` |
| `eo:common_name` | `common_name` |
| `eo:center_wavelength` | `center_wavelength` |
| `eo:full_width_half_max` | `full_width_half_max` |
| `gsd` | (not an `eo:bands` field) |

A collection without a band dimension does not get band summaries.

## 3. Consequences

- `GET /collections` and `GET /collections/{id}` show the same bands in the
  three lists. The band pickers show `SCL`, `WVP` and the angle bands.
- The upstream `summaries.eo:bands` is replaced. A band that is only in the
  upstream list, and not in `cube:dimensions`, is removed from the
  summaries. `load_collection` cannot read such a band, so this is correct.
- `BandSource` has a new optional `description` field.

## 4. Decision: data before visuals, common names as second names

On Planetary Computer, the `visual` asset declares its bands as
`B04`/`B03`/`B02` with `common_name` `red`/`green`/`blue` and the spectral
metadata of these bands. But the asset is an 8-bit true-color rendering.
Before this ADR, the cube gave these bands as `red`, `green` and `blue`
(ADR 0007). A user who loads `red` expects the red reflectance band, which
is the `B04` asset, but got the 8-bit band.

The project is in development (no production users), so we change the
names. Two rules are added to `resolve_asset_bands`:

1. **Copies of other assets.** If a band of a multi-band asset has a name
   that is the key of a different asset, the band is a copy of that asset.
   It gets only one name: its common name with the asset key as a prefix
   (`visual_red`). It does not get its STAC `name` as a second name
   (`visual_B04`), because that name looks too much like `B04`.
2. **Common names of single-band assets.** A data asset with exactly one
   band also gets the common name of the band as a second name, if no other
   single-band asset has the same common name. The asset key stays the main
   name.

Common names do not replace the asset keys. They are not unique (on
Planetary Computer, `B05`, `B06`, `B07` and `B8A` are all `rededge`; on CDSE,
`B02_10m`, `B02_20m` and `B02_60m` are all `blue`), and some bands do not
have one (`B09`, `SCL`, `AOT`, `WVP`). A common name that is already an
asset key (Earth Search `red`) or already a band name of a multi-band asset
(EOPF `blue`) does not change.

Result on Planetary Computer `sentinel-2-l2a`:

| Name | Reads |
| --- | --- |
| `B04`, `red` | the `B04` asset (reflectance) |
| `B03`, `green`; `B02`, `blue` | the `B03`/`B02` assets |
| `B08`, `nir`; `B01`, `coastal`; `B11`, `swir16`; `B12`, `swir22` | these assets |
| `B05`, `B06`, `B07`, `B8A` | these assets (no `rededge`) |
| `visual_red`, `visual_green`, `visual_blue` | bands 1, 2, 3 of `visual` (8-bit) |

The second names are in `cube:dimensions` and in both summaries, with the
metadata of their asset, so that discovery and the read path agree. A band
picker shows the same data two times (`B04` and `red`). EOPF already does
this (`red` and `b04`), and a process graph that uses `red` now reads
reflectance on Planetary Computer and on EOPF.

Discovery gives all the `item_assets` keys to the resolver, so it gives the
same names as the read path, which gives all the assets of the item.
