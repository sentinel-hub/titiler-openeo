# Core Concepts

This document explains the core concepts and data models used in openEO by TiTiler.

## Data Model

In openEO, a datacube is a fundamental concept and a key component of the platform. While traditional openEO implementations use multi-dimensional arrays for data representation, openEO by TiTiler simplifies this concept by focusing on image raster data that can be processed on-the-fly and served as tiles or as light dynamic raw data.

### Resolution and Dimension Management

The backend intelligently handles resolution and dimensions using these key principles:

1. **Default Resolution Control**: 
   - The `load_collection` and `load_collection_and_reduce` processes default to a width of 1024 pixels
   - This intentionally avoids loading data at native resolution by default, which could cause memory issues
   - Users can explicitly request native resolution by providing their own width/height parameters
   - The default provides a good balance between quality and performance

2. **Native Resolution Access**:
   - Resolution information is extracted from source metadata (transform or shape)
   - When width/height parameters are provided, proportions are maintained
   - Resolution is adjusted based on the requested spatial extent

3. **Early Resolution Optimization**:
   - Resolution is determined during initial data loading
   - Cropping adjusts resolution proportionally
   - CRS reprojection accounts for resolution changes

### Raster Data Model

The backend uses three primary data structures for efficient processing:

1. **ImageData**: Most processes use [`ImageData`](https://cogeotiff.github.io/rio-tiler/models/#imagedata) objects provided by [rio-tiler](https://cogeotiff.github.io/rio-tiler/) for individual raster operations. This object was initially designed to create slippy map tiles from large raster data sources and render these tiles dynamically on a web map. Each ImageData object inherently has two spatial dimensions (height and width).

![alt text](img/raster.png)

2. **RasterStack**: A dictionary mapping names/dates to ImageData objects, allowing for consistent handling of multiple raster layers. This is our implementation of the openEO data cube concept, with some key characteristics:
   - An empty data cube is represented as an empty dictionary (`{}`)
   - When there is at least one raster in the stack, it has a minimum of 2 dimensions (the spatial dimensions from the raster data)
   - Additional dimensions (like temporal or bands) can be added, but they must be compatible with the existing spatial dimensions
   - Spatial dimensions are inherent to the raster data and cannot be added separately

3. **LazyRasterStack**: An optimized version of RasterStack that lazily loads data when accessed. This improves performance by only executing processing tasks when the data is actually needed.

### Dimension Handling

The data cube implementation in openEO by TiTiler follows these principles for dimension handling:

1. **Spatial Dimensions**: Every raster in the stack has two spatial dimensions (height and width) that are inherent to the data. These dimensions cannot be added or removed through processes, as they are fundamental to the raster data structure.

2. **Additional Dimensions**: Non-spatial dimensions can be added to the data cube:
   - Temporal dimension: For time series data (e.g., "2021-01", "2021-02")
   - Bands dimension: For spectral bands (e.g., "red", "green", "blue")
   - Other dimensions: For any other type of categorization

3. **Dimension Compatibility**: When adding dimensions to a non-empty data cube, the new dimension must be compatible with the existing spatial dimensions. This means any ImageData added to the stack must match the height and width of existing rasters.

4. **Empty Data Cubes**: An empty data cube (`{}`) can receive any non-spatial dimension. The first raster data added to the cube will establish the spatial dimensions that all subsequent data must match.

### Data Reduction

The ImageData object is obtained by reducing as early as possible the data from the collections. While both `load_collection` and `load_collection_and_reduce` processes are available, it's recommended to use `load_collection_and_reduce` to immediately get an `imagedata` object at the desired resolution. This approach:

1. Uses a default width of 1024 pixels to prevent memory issues
2. Allows explicit control over resolution through width/height parameters
3. Performs data reduction at the target resolution
4. Maintains proper proportions throughout the process

![alt text](img/rasterstack.png)

The reduce process includes a parameter to choose the [pixel selection method](https://github.com/sentinel-hub/titiler-openeo/blob/main/titiler/openeo/processes/data/apply_pixel_selection.json#L24):

- `first` (default): selects the first pixel value
- `highest`, `lowest`: selects extreme values
- `mean`, `median`, `stddev`: statistical measures
- `lastbandlow`, `lastbandhigh`, `lastbandavg`: band-specific selections
- `count`: number of valid pixels

## Collections and STAC Integration

openEO by TiTiler integrates with external STAC API services to provide collections. It uses [`pystac-client`](https://github.com/stac-utils/pystac-client) to proxy the STAC API, configured through the `TITILER_OPENEO_SERVICE_STORE_URL` environment variable.

### OpenEO Process Graph to CQL2-JSON Conversion

The backend automatically converts OpenEO process graphs to CQL2-JSON format for STAC API filtering. Supported operators include:

- Comparison operators (`eq`, `neq`, `lt`, `lte`, `gt`, `gte`, `between`)
- Array operators (`in`, `array_contains`)
- Pattern matching operators (`starts_with`, `ends_with`, `contains`)
- Null checks (`is_null`)
- Logical operators (`and`, `or`, `not`)

Example conversion:
```json
// OpenEO process graph
{
  "cloud_cover": {
    "process_graph": {
      "cc": {
        "process_id": "lt",
        "arguments": {"x": {"from_parameter": "value"}, "y": 20}
      }
    }
  }
}

// Converted to CQL2-JSON
{
  "op": "<",
  "args": [{"property": "properties.cloud_cover"}, 20]
}
```

## Performance Considerations

The backend is optimized for on-the-fly processing and serving of raster data. Key considerations:

- Processing time increases with the extent of data
- Larger extents may lead to timeouts
- The backend can be easily replicated and scaled
- No additional middleware required for deployment
- Resolution is managed automatically to balance quality and performance
- Memory usage is controlled through:
  - Default width of 1024 pixels in load functions
  - Pixel count limits for larger requests
  - Early resolution optimization
