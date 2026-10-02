# Multi-band STAC Assets

Most catalogues give each band its own STAC asset (`B02`, `B03`, ... one
asset key per band). Some catalogues instead publish several bands inside
**one** asset — EOPF's Copernicus Sentinel-2 L2A collection stores all
twelve reflectance bands as one Zarr asset, `reflectance`:

```json
"reflectance": {
  "type": "application/vnd.zarr; version=3; profile=multiscales",
  "roles": ["data", "reflectance"],
  "bands": [
    {"name": "b01", "gsd": 20, "eo:common_name": "coastal"},
    {"name": "b04", "gsd": 10, "eo:common_name": "red"}
  ]
}
```

openEO by TiTiler resolves each band inside such an asset to its own,
independently addressable band name, transparently to `load_collection` —
`bands=["red"]` works exactly as if `red` were its own top-level asset, even
though the underlying request opens `reflectance` and reads one of its
Zarr variables.

## Two names per band

A band that declares **both** a common name (`eo:common_name`/`common_name`)
and its own STAC `name` is addressable by **either**. The `reflectance`
band above is valid as `bands=["red"]` *or* `bands=["b04"]` — `b04` being
the band's own identifier in the catalogue's metadata, the same value
Sentinel-2 documentation and tooling refer to it by.

A band with no separate `name` (its display name already came from `name`,
because no `eo:common_name`/`common_name` was declared) is addressable one
way only, as before.

## Where this shows up in collection discovery

Both names are advertised, not just accepted — a client should not need to
guess which one `load_collection` will honor:

- `cube:dimensions.spectral.values`, the datacube extension's band
  dimension
- `summaries.bands`, with per-band metadata (common name, wavelength,
  ground sample distance) attached identically under either name

`GET /collections/sentinel-2-l2a` lists `red` and `b04` side by side, each
with the same `gsd`/`eo:center_wavelength` values, rather than only the
common name. The asset key itself (`reflectance`) is never advertised or
directly addressable — only its individual bands are.

## Name collisions

If two different multi-band assets on the same item happen to publish the
same alias, neither silently wins: both are qualified as
`{asset_key}_{alias}` instead. This is independent per alias — a band's raw
`name` colliding elsewhere does not force its common name to qualify too,
and vice versa. An alias that is unique across the item is always left
bare.

## Worked example

Requesting the same band by its common name and by its own STAC name reads
identical pixels:

```json
{
  "process_graph": {
    "load1": {
      "process_id": "load_collection",
      "arguments": {
        "id": "sentinel-2-l2a",
        "spatial_extent": {"west": -1.5, "south": 62.0, "east": -1.0, "north": 62.3},
        "temporal_extent": ["2026-08-07T00:00:00Z", "2026-08-08T00:00:00Z"],
        "bands": ["b04"]
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

`"bands": ["red"]` in the same graph returns the same raster.

## Supported collections

Any STAC catalogue configured for this backend gets this automatically —
there is no per-collection configuration. An asset expands into its
individual bands when it declares two or more entries in `bands` (or the
legacy `eo:bands`) and carries no rendering role (`visual`, `overview`,
`thumbnail`); a true-colour composite like `TCI` still resolves as one
asset, since its bands are fixed RGB channels rather than independently
requestable data. Today this applies to EOPF's Sentinel-2 L2A collection
(`api.explorer.eopf.copernicus.eu/stac`); CDSE, Earth Search and Planetary
Computer publish one asset per band already, so nothing changes for them.

## Design reference

The full compatibility rules and rationale are documented in
[ADR 0007](https://github.com/sentinel-hub/titiler-openeo/blob/main/docs/adr/0007-multiband-assets.md),
which also covers why an asset's rendering role — not its band count alone —
decides whether it expands. ADRs are repo-only documentation (like
`docs/audits/`) and are not published on this site — follow the link on
GitHub.
