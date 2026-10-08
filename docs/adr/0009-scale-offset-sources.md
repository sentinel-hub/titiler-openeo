# ADR 0009 — Scale/offset sources and plugins

- **Status:** Proposed
- **Date:** 2026-10-08
- **Deciders:** @emmanuelmathot
- **Related:** [PR #322](https://github.com/sentinel-hub/titiler-openeo/pull/322), [PR #427](https://github.com/sentinel-hub/titiler-openeo/pull/427), [microsoft/PlanetaryComputer#134](https://github.com/microsoft/PlanetaryComputer/issues/134), [Element84/earth-search#9](https://github.com/Element84/earth-search/issues/9), [#66](https://github.com/Element84/earth-search/issues/66), [#71](https://github.com/Element84/earth-search/issues/71), [#41](https://github.com/Element84/earth-search/issues/41)

---

## 1. Context

`load_collection` and `load_stac` must return physical values, for example
Sentinel-2 L2A reflectance from 0 to 1, and not raw DN. PR #322 applied the
STAC `raster:scale`/`raster:offset`. That is not sufficient, because the
catalogues do not publish the values in the same place. Checked live on
2026-10-08 for `sentinel-2-l2a`:

| Catalogue, collection | STAC | COG header | Product metadata (`MTD_MSIL2A.xml`) |
| --- | --- | --- | --- |
| Microsoft Planetary Computer `sentinel-2-l2a` | none | 1/0 | `product-metadata`, all items |
| CDSE `sentinel-2-l2a` | `0.0001` / `-0.1` | 1/0 (JP2) | `product_metadata`, same values as STAC |
| Element84 Earth Search `sentinel-2-c1-l2a` | `0.0001` / `-0.1` | `0.0001` / `-0.1` | `product_metadata` |
| Element84 Earth Search `sentinel-2-l2a` | `-0.1` also on items whose pixels already had the offset removed | 1/0 | `product_metadata` (requester-pays), new items only |

Planetary Computer makes its own L2A with Sen2Cor
([microsoft/PlanetaryComputer discussion #40](https://github.com/microsoft/PlanetaryComputer/discussions/40)) and does not publish the
offset in STAC or in the COG header. This problem is open since 2022, with no
planned fix ([microsoft/PlanetaryComputer#134](https://github.com/microsoft/PlanetaryComputer/issues/134)). The only correct source there is
each product's `MTD_MSIL2A.xml`:

    reflectance = (DN + BOA_ADD_OFFSET[band]) / BOA_QUANTIFICATION_VALUE

`BOA_ADD_OFFSET` is -1000 from baseline 04.00 (2022-01-25) and is not present
before. A fixed offset or a date cutoff is thus wrong for part of the archive.

Earth Search's legacy `sentinel-2-l2a` shows the opposite problem. Many of
its items had the 1000 DN offset removed from the pixels, but STAC still
declares `-0.1`, and the `earthsearch:boa_offset_applied` flag is sometimes
wrong too ([Element84/earth-search#9](https://github.com/Element84/earth-search/issues/9), [#66](https://github.com/Element84/earth-search/issues/66), [#71](https://github.com/Element84/earth-search/issues/71)). On a clear area, its raw B04 DN
was 797 against 1797 for the same product on Planetary Computer and on
`sentinel-2-c1-l2a`. No metadata describes those pixels correctly, so that
collection is not supported; deployments use `sentinel-2-c1-l2a`, as an
Element84 contributor recommends ([Element84/earth-search#41](https://github.com/Element84/earth-search/issues/41)).

Other missions can have the same problem. The core must not contain provider
names (the reason for issue #377).

## 2. Decision

### 2.1 Sources are an ordered list for each deployment and collection

A source is `"stac"`, `"cog"` or the name of a plugin. The deployment sets a
global order (`TITILER_OPENEO_PROCESSING_SCALE_OFFSET_SOURCES`, default
`stac, sentinel2-boa, cog`). It can replace that order for one collection
(`TITILER_OPENEO_PROCESSING_SCALE_OFFSET_COLLECTIONS`). An empty list means no
scale/offset.

For each band, the reader tries the sources in order. The first source with a
pair that is not 1/0 wins, so a band is scaled one time only. A source that
does not apply to the item is skipped.

Thus the deployment states what it knows about its catalogue. Examples:
CDSE `["stac"]`, Planetary Computer `["sentinel2-boa"]`. The code has no
provider hostnames.

### 2.2 The COG header values come from rio-tiler, without `unscale=True`

rio-tiler reads with `unscale=False` and keeps the header values on
`ImageData.scales`/`offsets`. With `unscale=True`, rio-tiler applies them and
then sets them to 1/0, so a later step cannot know that a band is already
scaled. That would scale a band two times when STAC and the header both have
values. A single step after the read selects one pair for each band and does
the same float32 in-place calculation as rio-tiler. It was measured as equally
fast and uses less memory (the stack is still uint16 when it is converted).

### 2.3 Plugins

A plugin (`titiler/openeo/scaleoffset/`) has:

- a collection pattern;
- a metadata asset pattern, media types and roles, matched in the same way as
  a band source (ADR 0002);
- `load(href, fetch_href, fetcher)`, which returns `{band key: (scale, offset)}`;
- `band_key(asset fields)`.

The reader signs the asset href with the item's signer and fetches it with the
band-source fetcher. This is the same path as the Sentinel-2 angle bands
(ADR 0004) and the SAR annotations (ADR 0001). The cache key is the unsigned
href, so a renewed SAS token does not empty the cache.

Two rules keep the cost low:

- A plugin's file is loaded only for a band that the plugin knows: its
  `band_key` is not None, and `band_key` needs no fetch.
- The file is loaded only when no earlier source has a value for that band.

With the default order on Earth Search or CDSE, STAC covers the reflectance
bands, and `SCL` has no key. Thus no XML is fetched.

The first plugin is `sentinel2-boa`:

- It parses `BOA_QUANTIFICATION_VALUE`, `BOA_ADD_OFFSET` (offset 0 when there
  is none) and `Spectral_Information`, and also `AOT`/`WVP`.
- It maps a data asset to a physical band by its `eo:bands[0].name`. This
  name is `B04` on all three catalogues, whatever the asset key.
- It was checked on baselines 02.12, 03.00 and 05.13.

Source names are a `Literal` in `settings.py`, so an unknown name fails at
startup. A test makes sure that the `Literal` and the plugin registry agree.

### 2.4 Failures

A plugin that applies but cannot fetch or parse its file raises. The read
fails and does not return raw DN without an error. `_reader` retries only on
`RasterioIOError`, so such an error is not retried.

## 3. Consequences

- Planetary Computer `sentinel-2-l2a` gives harmonized 0–1 reflectance for
  all baselines. On the same clear area, its B04 median reflectance agreed
  with CDSE and Earth Search `sentinel-2-c1-l2a` within 0.002 for 2026 and
  2021 products.
- A new mission with the same problem needs one plugin module, one entry in
  `PLUGINS` and one name in the `Literal`.
- Behavior change: the COG source and the plugin are on by default. A
  catalogue whose COG header or product XML has values that STAC does not have
  now gives physical values. A process graph that scales by hand will then
  scale two times. To keep the old behavior, list only `stac`, or set
  `APPLY_SCALE_OFFSET=false`.
- `dataset_statistics` are not scaled (`unscale=True` would scale them).
- SAR calibration still needs raw DN (ADR 0001). The Sentinel-1 GRD
  measurement headers on Planetary Computer and CDSE have scale 1 and offset 0
  (checked 2026-10-08), and no plugin matches Sentinel-1, so nothing changes
  for `sar_backscatter`.
