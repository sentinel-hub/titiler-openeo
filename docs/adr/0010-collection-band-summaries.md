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

## 4. Decision: `visual_red`, not `red`, on Planetary Computer

On Planetary Computer, the `visual` asset declares its bands as
`B04`/`B03`/`B02` with `common_name` `red`/`green`/`blue` and the spectral
metadata of these bands. But the asset is an 8-bit true-color rendering.
Before this ADR, the cube gave these bands as `red`, `green` and `blue`
(ADR 0007). A user can think that `red` is the red reflectance band, which
is the `B04` asset.

`resolve_asset_bands` now has one more rule. If a band of a multi-band asset
has a name that is the key of a different asset, the band is a copy of that
asset. All the names of this band get the asset key as a prefix:

| Before | After |
| --- | --- |
| `red`, `green`, `blue` | `visual_red`, `visual_green`, `visual_blue` |
| `B04` of `visual` (hidden by the `B04` asset) | `visual_B04`, `visual_B03`, `visual_B02` |

Discovery and the read path use the same resolver, so they give the same
names. Discovery gives all the `item_assets` keys to the resolver, so it
qualifies the same bands as the read path.

The bands of EOPF `reflectance` do not change: no band name is an asset
key.

This is a breaking change on Planetary Computer: a process graph that loads
`red`, `green` or `blue` from `sentinel-2-l2a` must use `visual_red`,
`visual_green` and `visual_blue`, or the reflectance bands `B04`, `B03`
and `B02`.
