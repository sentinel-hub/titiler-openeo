# Virtual Bands

Some collections need bands that do **not** exist as raster assets in the source
STAC catalog. A common example: Sentinel-2 view/sun angle bands
(`viewZenithMean`, `sunZenithAngles`, ...) used by SentinelHub-style eval scripts.
In catalogs such as the CDSE Sentinel-2 catalog these are not raster files — they
exist only as per-scene scalar properties (`view:incidence_angle`, `view:azimuth`,
`view:sun_azimuth`). Reconstructing them is therefore **collection-specific**.

The **virtual bands** mechanism lets you declare such bands through a plugin that is
bound to a collection in configuration. A virtual band is:

- **advertised** in the collection's `cube:dimensions` band dimension, so openEO
  clients accept it as a valid band name; and
- **computed lazily** at read time from STAC item metadata and/or the pixel values
  of real bands, only when (and if) a data slice is actually materialized.

## Configuration

Set `TITILER_OPENEO_VIRTUAL_BANDS_CONFIG` to the path of a JSON file mapping
collection ids to a list of plugin bindings:

```json
{
  "sentinel-2-l2a": [
    {
      "plugin": "normalized_difference",
      "options": {"name": "NDVI", "a": "B08_10m", "b": "B04_10m"}
    },
    {
      "plugin": "constant_from_property",
      "options": {"name": "viewZenithMean", "property": "view:incidence_angle"}
    }
  ]
}
```

Each entry references a `plugin` by its **entry-point name** (see below) and passes
`options` as keyword arguments to the plugin.

### Kubernetes / Helm

The bundled Helm chart wires this for you. Point `stac.virtualBandsConfig` at a
JSON file in the chart (a sample lives at `files/virtual_bands.json`):

```yaml
stac:
  apiUrl: "https://stac.dataspace.copernicus.eu/v1"
  virtualBandsConfig: "files/virtual_bands.json"
```

The chart embeds the file in the ConfigMap (mounted at `/config`) and sets
`TITILER_OPENEO_VIRTUAL_BANDS_CONFIG=/config/virtual_bands.json`. When the value
is empty (the default), nothing is mounted and the feature is disabled.

!!! note
    The referenced plugins must be registered as `titiler.openeo.virtual_bands`
    entry points **in the running image**. The built-in examples ship with
    titiler-openeo; custom plugins require building an image that installs your
    plugin package.

## Built-in example plugins

| Entry-point name         | Class                        | Computes from        |
|--------------------------|------------------------------|----------------------|
| `normalized_difference`  | `NormalizedDifferencePlugin` | two real bands `(a-b)/(a+b)` |
| `constant_from_property` | `ConstantFromPropertyPlugin` | a scalar STAC item property, broadcast over the grid |

`constant_from_property` shows how to wire values that are **not** raster assets:
the value is read from `item.properties[...]` and broadcast to the output grid. It
is the generic pattern for reconstructing per-scene angle bands.

!!! note
    A purely property-derived band has no real band of its own, so request it
    **alongside at least one real band** (e.g. `["SCL", "viewZenithMean"]`). The
    real band anchors the output grid (shape, CRS, bounds); without it the loader
    raises an error.

## Writing a plugin

Plugins subclass `VirtualBandPlugin` and are discovered through the
`titiler.openeo.virtual_bands` entry-point group.

```python
from typing import List
import numpy, pystac
from rio_tiler.models import ImageData
from titiler.openeo.virtualbands import BandMetadata, VirtualBandPlugin, band_array


class MyIndexPlugin(VirtualBandPlugin):
    def __init__(self, name: str, a: str, b: str, **options):
        super().__init__(name=name, a=a, b=b, **options)
        self.name, self.a, self.b = name, a, b

    def provided_bands(self) -> List[BandMetadata]:
        return [BandMetadata(name=self.name, description="my index")]

    def required_bands(self) -> List[str]:
        # Real bands the loader must read so they are available in `compute`.
        return [self.a, self.b]

    def compute(self, name: str, items: List[pystac.Item], image: ImageData):
        a = band_array(image, self.a)   # look bands up by name
        b = band_array(image, self.b)
        return (a - b) / (a + b)
```

Register it via entry points in your package's `pyproject.toml`:

```toml
[project.entry-points."titiler.openeo.virtual_bands"]
my_index = "my_package.plugins:MyIndexPlugin"
```

### The plugin interface

- `provided_bands() -> list[BandMetadata]` — band(s) added to the collection.
- `required_bands() -> list[str]` — real asset bands the loader must read so they
  are present in `compute`'s `image`. Empty for purely metadata-derived bands.
- `compute(name, items, image) -> ndarray` — return a `(H, W)` (or `(1, H, W)`)
  array aligned to `image`. `items` are the STAC items for the slice (use
  `item.properties` for metadata-derived values); `image.band_names` are the real
  band names, so `band_array(image, name)` looks them up.

## How it works

- **Metadata**: the registry augments each collection's `cube:dimensions` band
  dimension with the provided band names (`stacApiBackend._augment_with_virtual_bands`).
- **Reading**: `load_collection` splits the requested bands into real, virtual, and
  *support* bands (real bands needed only to compute requested virtual bands). It
  reads `real + support`, then — inside the lazy per-slice task — computes each
  requested virtual band and reorders the output to exactly the requested band
  order, dropping support-only bands.
- **Laziness**: `compute` runs only when a slice is materialized, and only for
  bands the user actually requested. Collections with no bound plugins follow the
  original code path unchanged.
