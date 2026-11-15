# Examples

This section provides practical examples of using openEO by TiTiler for various Earth Observation tasks.

## Web Editor Examples

The openEO Web Editor provides a graphical interface for interacting with the API. To get started:

1. Start the services:

```bash
echo "TITILER_OPENEO_AUTH_USERS='{\"test\":{\"password\":\"test\"}}'" >> .env   # local development only
docker compose up
```

1. Access the editor at http://localhost:8080 and set the backend URL to http://localhost:8081

2. Log in with the local development user that the `echo` line adds (username `test`, password `test`). For other authentication setups, see the [Admin Guide](../admin-guide.md#authentication)

## Jupyter Notebook Examples

We provide several Jupyter notebooks demonstrating different use cases:

### [Manhattan Satellite Imagery](../notebooks/manhattan.ipynb)

Learn how to:

- Connect to the openEO backend
- Load and process Sentinel-2 imagery using default resolution (1024 pixels width)
- Control output resolution with explicit width/height parameters
- Create true-color RGB visualizations
- Apply color enhancements for better visualization

### [NDVI Time Series Analysis](../notebooks/ndvi_time_series.ipynb)

Explore how to:

- Calculate vegetation indices (NDVI)
- Extract time series data for specific areas
- Control memory usage with appropriate resolution settings
- Analyze temporal patterns in vegetation
- Visualize results using matplotlib

### [Field Monitoring with Vector Data Cubes](../notebooks/vector_field_monitoring.ipynb)

Learn how to (see [Vector Data Cubes](../vector-cube.md)):

- Load field boundaries from a FlatGeobuf file with `load_url`
- Move the field edges inward with `vector_reproject` and `vector_buffer`
- Compute a cloud-free NDVI time series per field with `aggregate_spatial`
- Process the vector data cube: `filter_temporal`, `reduce_dimension`, `merge_cubes`
- Select fields with `filter_bbox` and `filter_vector`
- Download the results as GeoParquet, GeoPackage, FlatGeobuf, Shapefile and CSV

### [From Rasters to Polygons and Back](../notebooks/vector_raster_bridges.ipynb)

Learn how to:

- Make polygons from the Copernicus crop type map with `raster_to_vector`
- Compute zonal statistics on these polygons
- Put the polygons on the Sentinel-2 grid with `vector_to_raster`
- Crop and mask a raster to fields with `filter_spatial`
- Process each field separately with `apply_polygon`

### Resolution Control Examples

The examples demonstrate different approaches to managing resolution:

1. **Default Resolution** (Recommended):

```python
loadcol = load_collection(
    "SENTINEL2_L2A",
    spatial_extent=bbox,
    temporal_extent=["2021-01-01", "2021-12-31"],
    bands=["B04", "B08"]
)
# Reduce temporal dimension
reduced = reduce_dimension(loadcol, dimension="time", reducer="first")
# Uses default width of 1024 pixels for memory efficiency
```

2. **Custom Resolution**:

```python
loadcol = load_collection(
    "SENTINEL2_L2A",
    spatial_extent=bbox,
    temporal_extent=["2021-01-01", "2021-12-31"],
    bands=["B04", "B08"],
    width=2048,  # Explicitly control resolution
)
reduced = reduce_dimension(loadcol, dimension="time", reducer="first")
```

## Running the Notebooks

To run the notebooks locally:

1. Install the development dependencies:

```bash
python -m pip install -e ".[dev]"
```

2. Start Jupyter:

```bash
jupyter notebook docs/notebooks
```

3. Open the desired notebook and follow the instructions


## Parameter Management

For examples of using the comprehensive parameter management system (query parameters, defaults, validation), see the [Parameter Management](../parameter-management.md#examples) documentation.

