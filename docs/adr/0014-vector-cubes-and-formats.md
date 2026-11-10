# ADR 0014 — Vector data cubes and vector output formats

- **Status:** Proposed
- **Date:** 2026-11-10
- **Deciders:** @emmanuelmathot, Vincent (review)
- **Related:** [#443](https://github.com/sentinel-hub/titiler-openeo/pull/443)
  (output format registry), [#476](https://github.com/sentinel-hub/titiler-openeo/pull/476)
  (Python 3.14 image), [geopandas/pyogrio#705](https://github.com/geopandas/pyogrio/issues/705)

---

## 1. Context

### 1.1 The need

Users want to:

- make vector data from a raster, for example polygons from a
  classification;
- use vector data in other processes: zonal statistics, buffer, filter,
  rasterize back;
- download vector data in the usual GIS formats: GeoJSON, GeoParquet,
  GeoPackage, Shapefile, FlatGeobuf.

### 1.2 What exists

- There is no vector data cube type. `aggregate_spatial`
  (`processes/implementations/spatial.py`) returns a GeoJSON
  `FeatureCollection` dict. Each feature has a `values` property, keyed by
  `datetime`.
- The output format registry (`processes/implementations/formats.py`, #443)
  has a `vector` data kind with JSON, GeoJSON and CSV encoders. An encoder
  returns one `bytes` blob. There is no file name, and no encoder reads
  `options`.
- Geometries are plain GeoJSON dicts in EPSG:4326. There is no shapely,
  OGR or Parquet library at runtime.
- The `input` section of `GET /file_formats` is empty.

### 1.3 What the openEO specification says

- **There is no `vector-cube` subtype.** openEO processes 2.0 replaced the
  `raster-cube` and `vector-cube` subtypes with `datacube` and dimension
  constraints. A vector cube is
  `{"type": "object", "subtype": "datacube", "dimensions": [{"type": "geometry"}]}`.
  `aggregate_spatial`, `filter_vector` and `vector_buffer` use this form.
- **openEO has no `vectorize` process.** The VITO backend and the openEO
  Python client (`DataCube.raster_to_vector()`) use the non-standard
  `raster_to_vector` and `vector_to_raster`. Their VITO specs still use the
  old subtypes.
- **A raster cube and a vector cube cannot merge.** `merge_cubes`: "It is
  not possible to merge a vector and a raster data cube." Two vector cubes
  can merge only if they have the same base geometry type (or its multi
  type). Otherwise the process raises `IncompatibleGeometryTypes`.
- **Bridge processes** are the only way between the two kinds:

  | Direction | Processes |
  |---|---|
  | raster + vector → raster | `mask_polygon`, `filter_spatial`, `apply_polygon`, `spatial_extent` of `load_collection` / `load_stac` |
  | raster + vector → vector | `aggregate_spatial`; `raster_to_vector` (non-standard) |
  | vector → raster | `vector_to_raster` (non-standard) |
  | either kind | `filter_bbox` |

- **Dimension-generic processes** accept any `datacube`, so they apply to a
  vector cube too, on its non-geometry dimensions: `apply`,
  `apply_dimension`, `reduce_dimension`, `filter_labels`, `rename_labels`,
  `add_dimension`, `merge_cubes`, `save_result`, and others. The temporal
  processes and `filter_bands` apply when the cube has a `t` or `bands`
  dimension.

## 2. Decision

### 2.1 The `VectorCube` model

A small dataclass in `processes/implementations/vector_model.py`. It does
not use a GeoDataFrame or xarray.

```python
@dataclass
class VectorCube:
    geometries: list[dict]          # GeoJSON geometry dicts, in `crs`
    crs: rasterio.crs.CRS
    properties: list[dict]          # attributes of each feature (not a dimension)
    values: numpy.ma.MaskedArray    # shape (n_geometries, *len(labels[d]) for d in dims[1:])
    dims: tuple[str, ...] = ("geometry",)   # for example ("geometry", "t", "bands")
    labels: dict[str, list] = field(default_factory=dict)
```

- `from_geojson()` reads a FeatureCollection, a Feature or a Geometry.
- `to_feature_collection()` writes the same JSON as `aggregate_spatial`
  writes now, in EPSG:4326 (RFC 7946). `__geo_interface__` returns it.
- `to_records()` writes the flat table for the GIS formats: one row for each
  (geometry, `t` label), and one column for each label of the other
  dimensions. This is the layout of the CSV output now.

`VectorCube` is the Python name only. The process specs use `datacube` with
a `geometry` dimension.

### 2.2 `aggregate_spatial` returns a `VectorCube`

The dimensions are `("geometry", "t"[, "bands"][, target_dimension])`. The
JSON, GeoJSON and CSV output does not change, byte for byte. The
`geometries` parameter also accepts a `VectorCube`, for zonal statistics on
the polygons from `raster_to_vector`.

Where a geometry parameter is typed `Dict` (`mask_polygon.mask`,
`spatial_extent`, and so on), the `@process` decorator gives
`to_feature_collection()` in place of the `VectorCube`.

### 2.3 Processes

| Process | Library | Note |
|---|---|---|
| `raster_to_vector(data)` | rasterio (`features.shapes`) | Non-standard (VITO name). 4-connected zones of equal value. One band is required. One polygon set for each date. |
| `vectorize(data)` | same | Alias of `raster_to_vector`: a second spec, same implementation |
| `vector_to_raster(data, target)` | rasterio (`features.rasterize`) | Non-standard (VITO name), on the grid of `target` |
| `load_geojson(data, properties)` | none | openEO proposal |
| `load_url(url, format)` | see §2.6 | Vector formats added. COG does not change |
| `vector_reproject` | rasterio (`warp.transform_geom`) | openEO proposal |
| `vector_buffer`, `filter_vector` | shapely | openEO proposals |
| `filter_spatial`, `apply_polygon` | rasterio | openEO core, not implemented now |

The specs come from openeo-processes `proposals/` and from the VITO
non-standard specs, rewritten to `datacube` with dimensions. All of them
have `"experimental": true`.

### 2.4 Raster and vector cubes together

1. **Clear errors.** A vector cube given to a raster-only process (for
   example `resample_spatial`, `ndvi`, `apply_kernel`, `mask`), or
   `merge_cubes(raster, vector)`, gives `ProcessParameterInvalid` (400)
   with a message that names the two cube kinds. The `merge_cubes` message
   quotes the specification.
2. **Generic processes on vector cubes:** `reduce_dimension`, `apply`,
   `filter_labels`, `filter_temporal`, `filter_bands`, `filter_bbox`,
   `rename_labels`. They operate on the `values` array along the axis of
   the dimension. Reducing the `geometry` dimension is an error. The other
   generic processes give a clear "not supported for vector data cubes yet"
   error.
3. **`merge_cubes` of two vector cubes**, with `IncompatibleGeometryTypes`
   and `OverlapResolverMissing`, as in the specification.

### 2.5 Output and input formats

The registry from #443 gets these changes:

- `OutputFormat.extension`, and `OutputFormat.requires` (module names). A
  format whose modules are not installed is not listed in `/file_formats`.
  `save_result` then gives `FormatUnsuitable` with an install hint.
- `SaveResultData.filename`. `POST /result` sets `Content-Disposition`
  when it is set.
- The encoders read `options`.
- `/file_formats` gets an `input` section.

| Format | Media type | Writer | CRS |
|---|---|---|---|
| GeoJSON | `application/geo+json` | stdlib `json` | EPSG:4326 |
| CSV | `text/csv` | stdlib `csv` | no geometry |
| GeoParquet 1.1 | `application/vnd.apache.parquet` | arro3 + shapely WKB | native |
| GeoPackage | `application/geopackage+sqlite3` | pyogrio, in memory | native |
| FlatGeobuf | `application/vnd.flatgeobuf` | pyogrio, in memory | native |
| ESRI Shapefile | `application/zip` | pyogrio, then zip | native |

### 2.6 Dependencies

The core uses rio-tiler and rasterio only. All other libraries go in the
optional extra `vector`, which the Docker image installs:

```toml
vector = ["pyogrio>=0.13", "shapely>=2.0", "arro3-core", "arro3-io"]
```

| Core (no new dependency) | Extra `vector` |
|---|---|
| `VectorCube`, `raster_to_vector`, `vectorize`, `vector_to_raster`, `vector_reproject`, `filter_spatial`, `apply_polygon` | `vector_buffer`, `filter_vector` (shapely) |
| `load_geojson`; `load_url` for GeoJSON | `load_url` for GeoPackage, FlatGeobuf, Shapefile (pyogrio), GeoParquet (arro3) |
| JSON, GeoJSON and CSV output | GeoPackage, FlatGeobuf, Shapefile (pyogrio), GeoParquet (arro3) output |

The code imports the extra modules inside the functions that use them, and
never at module import.

- **pyogrio, not fiona.** fiona 1.10.1 (2024-09) has no Python 3.14 wheel,
  and the image runs Python 3.14 (#476). Its new wheel build (Toblerity/Fiona
  #1531) has no release yet.
- **arro3, not pyarrow.** The pyogrio wheel has no Parquet driver. pyarrow
  takes 166 MB installed; arro3-core and arro3-io take 35 MB. arro3 writes
  the `geo` key in the Parquet file metadata, with
  `skip_arrow_metadata=True`, so that pyarrow and geopandas read it.

### 2.7 Limit

`ProcessingSettings.max_vector_features` (default 100 000). `raster_to_vector`
and `load_url` give a 400 error above this count. A noisy float raster can
make one polygon for each pixel.

## 3. The second GDAL (spike, 2026-11-10)

rasterio cannot write vector files. pyogrio brings a second GDAL in its
wheel. The spike ran rasterio and pyogrio in one process, in both import
orders. The scripts were not kept.

| Check | Linux venv (Python 3.14) | Wolfi image (builder stage) |
|---|---|---|
| Versions | rasterio 1.5.0 (GDAL 3.12.1, PROJ 9.7.1); pyogrio 0.13.0 (GDAL 3.12.4); pyproj 3.7.2 (PROJ 9.5.1) | same |
| GeoTIFF writes with deflate, LERC, zstd; read back | pass, no loss | pass, no loss |
| GPKG, FlatGeobuf (file and memory), Shapefile (file) in EPSG:32631 | pass | pass |
| CRS round trip rasterio → pyogrio → rasterio (EPSG 4326, 32631, 3035, 3857) | equal | equal |
| `rasterio.warp` after pyogrio did PROJ work | pass | pass |
| `PROJ_DATA`, `PROJ_LIB`, `GDAL_DATA` set by an import | none | none |
| Full test suite, pyogrio imported first | 1885 passed, 11 skipped | not run |
| GeoParquet from arro3, read by pyarrow and geopandas | CRS, geometry and columns equal | not run |
| `/opt/venv` size | | 385 MB → 527 MB (+142 MB); +308 MB with pyarrow |

**macOS was not tested.** pyogrio#705: with the 0.13.0 macOS wheels,
`import pyogrio` before `rasterio` aborts a LERC write in rasterio, because
the pyogrio libgdal exports the symbols of its static libraries. Linux is
not affected, because Python loads the libraries of each extension
privately. The fix (pyogrio#706, symbols hidden in the macOS and Linux
wheels) is merged, but it is not in a release. Mitigations:

- In titiler-openeo, rasterio is always imported first (titiler-core imports
  it at startup), and pyogrio is imported only in the functions that use it.
- Increase the pin to the first pyogrio release that contains #706.

An image with one GDAL (rasterio and pyogrio built from source against a
system GDAL) is possible later. It is not necessary now.

## 4. Consequences

- `aggregate_spatial` returns a new type. Code that reads the dict directly
  (not through `save_result`) must change. The output bytes do not change.
- The image is 142 MB larger. A deployment that does not install the extra
  keeps GeoJSON, JSON and CSV output, and all the core processes.
- Two GDAL copies are in the image. A GDAL configuration option set with
  `rasterio.Env` does not apply to pyogrio, and the reverse is also true.
- Shapefile field names have a maximum of 10 characters, and Shapefile
  stores dates as text. The format description says this.
- `raster_to_vector`, `vectorize` and `vector_to_raster` are non-standard.
  A process graph that uses them works on VITO, but not on all backends.

## 5. Delivery

Stacked PRs. Each one can be reviewed alone:

1. `VectorCube` and `aggregate_spatial` on it (§2.1, §2.2). No new
   dependency.
2. `raster_to_vector` and `vectorize` (§2.3, §2.7).
3. The `vector` extra and the GeoParquet, GeoPackage, FlatGeobuf and
   Shapefile output (§2.5, §2.6).
4. `load_geojson`, vector `load_url`, and the `/file_formats` input section.
5. `vector_reproject`, `vector_buffer`, `filter_vector`.
6. `vector_to_raster`.
7. Clear errors for vector cubes in raster-only processes (§2.4.1).
8. Generic processes on vector cubes (§2.4.2).
9. `merge_cubes` for vector cubes (§2.4.3).
10. `filter_spatial` and `apply_polygon`. This PR does not need the vector
    cube.

## 6. Non-goals

- **CoverageJSON output.** It comes later, through
  [titiler-covjson](https://github.com/developmentseed/titiler-covjson), when
  that project has a PyPI release, a Grid with a `t` axis and a
  CoverageCollection. The polygons from `raster_to_vector` are not a
  coverage, so they stay out of scope.
- Batch jobs. All results are still synchronous.
- Keeping the `aggregate_spatial` geometries that cover no pixel (as the
  specification says). This is a separate change in behavior.
