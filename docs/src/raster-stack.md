# RasterStack Data Model

In titiler-openeo, the RasterStack data model serves as the foundational data structure for handling Earth Observation datasets. This document explains the enhanced RasterStack concept, its implementation with time dimension support, and the performance benefits it provides.

## Overview

The RasterStack is a dictionary-like structure that organizes raster data along multiple dimensions, primarily **time** and **spectral bands**. Each entry in the RasterStack contains an `ImageData` object representing a multi-band image (2D or 3D) at a specific time point.

```python
# Example of time-organized RasterStack structure
RasterStack = {
    "2023-01-01": ImageData(...),  # Multi-band image for first date
    "2023-01-15": ImageData(...),  # Multi-band image for second date  
    "2023-02-01": ImageData(...),  # Multi-band image for third date
}
```

## Dimensional Model

The RasterStack defines a clear dimensional hierarchy:

1. **Time Dimension (Primary)**: RasterStack keys represent temporal organization
2. **Spectral Dimension (Secondary)**: Each ImageData contains multiple bands
3. **Spatial Dimensions**: Each band contains 2D spatial data (height, width)

```python
# Time dimension: multiple temporal observations
temporal_stack = {
    "2023-01-01": ImageData(array.shape=(4, 512, 512)),  # 4 bands
    "2023-02-01": ImageData(array.shape=(4, 512, 512)),  # 4 bands
}

# Each ImageData represents multi-band observations at one time point
single_observation = temporal_stack["2023-01-01"]
# single_observation.array.shape = (bands, height, width) = (4, 512, 512)
```

## ImageData vs RasterStack

- **ImageData**: Multi-band raster data for a single time point with spatial extent, CRS, and band metadata
- **RasterStack**: Time-organized collection of ImageData objects, enabling temporal analysis and processing

## LazyRasterStack with Temporal Intelligence

The LazyRasterStack extends the basic RasterStack concept with sophisticated time-aware lazy loading and concurrent execution capabilities:

```python
# LazyRasterStack with timestamp support
raster_stack = LazyRasterStack(
    tasks=tasks,
    key_fn=lambda asset: asset["id"],
    timestamp_fn=lambda asset: asset["datetime"],  # Enable temporal features
    max_workers=5  # Concurrent execution
)

# Temporal access and grouping
temporal_groups = raster_stack.groupby_timestamp()
single_date_data = raster_stack.get_by_timestamp(datetime(2023, 1, 1))
```

### Key Features of Enhanced LazyRasterStack

1. **Temporal Organization**: Automatic sorting and grouping by timestamps for time-series analysis
2. **Concurrent Execution**: Parallel loading of data using ThreadPoolExecutor for improved performance
3. **Timestamp-based Access**: Direct access to observations by time periods
4. **Intelligent Caching**: Per-key caching to avoid redundant computations
5. **Lazy Evaluation**: Data loaded only when accessed, reducing memory footprint
6. **Multi-band Support**: Each temporal observation can contain multiple spectral bands

### Temporal Processing Capabilities

```python
# Access specific time periods
jan_data = raster_stack.get_by_timestamp(datetime(2023, 1, 1))

# Process data chronologically 
for timestamp in raster_stack.timestamps():
    temporal_group = raster_stack.get_by_timestamp(timestamp)
    # Each temporal_group contains all bands for that time point
    
# Efficient time-series operations
first_observation = raster_stack[raster_stack.keys()[0]]  # Earliest
last_observation = raster_stack[raster_stack.keys()[-1]]   # Latest
```

## Advantages of the Enhanced RasterStack Model

- **Temporal Consistency**: Standardized time-first organization for all Earth Observation workflows
- **Multi-dimensional Support**: Explicit handling of time and spectral dimensions
- **Concurrent Performance**: Parallel data loading reduces processing time for large datasets
- **Memory Efficiency**: LazyRasterStack with intelligent caching minimizes memory usage
- **Scalability**: Efficient handling of time-series data with hundreds of temporal observations
- **Predictability**: Standardized multi-dimensional structure across all operations

## Dimensional Processing Patterns

### Temporal Dimension Operations

```python
# Reduce across time (e.g., temporal mean)
from titiler.openeo.processes.implementations.reduce import reduce_dimension

temporal_mean = reduce_dimension(
    data=raster_stack,
    reducer=mean_reducer,
    dimension="temporal"
)
# Result: Single ImageData with time-averaged bands
```

### Spectral Dimension Operations  

```python
# Reduce across bands (e.g., NDVI calculation)
ndvi_stack = reduce_dimension(
    data=raster_stack, 
    reducer=ndvi_calculator,
    dimension="spectral"
)
# Result: RasterStack with single-band NDVI for each time point
```

### Combined Processing

```python
# Apply pixel selection across temporal groups
from titiler.openeo.processes.implementations.reduce import apply_pixel_selection

# Mosaic overlapping observations at each time point
mosaicked_stack = apply_pixel_selection(
    data=raster_stack,
    pixel_selection="first"  # Uses temporal grouping automatically
)
```

## How Multi-dimensional Processing Works

### Load Phase - Temporal Organization

Data is loaded and organized temporally into a LazyRasterStack:

```python
# Process graph example - loads multi-band time series
{
  "process_id": "load_collection",
  "arguments": {
    "id": "sentinel-2-l2a",
    "spatial_extent": {...},
    "temporal_extent": ["2023-01-01", "2023-03-01"],
    "bands": ["B02", "B03", "B04", "B08"]  # Blue, Green, Red, NIR
  }
}
# Results in LazyRasterStack with temporal keys, each containing 4-band ImageData
```

### Process Phase - Dimension-aware Operations

Operations are applied respecting dimensional structure:

```python
# Spectral processing within each time point
{
  "process_id": "normalized_difference", 
  "arguments": {
    "x": {"from_node": "load_collection", "band": "B08"},  # NIR
    "y": {"from_node": "load_collection", "band": "B04"}   # Red
  }
}
# Produces single-band NDVI for each temporal observation
```

### Temporal Analysis

```python
# Time-series analysis across the temporal dimension
{
  "process_id": "reduce_dimension",
  "arguments": {
    "data": {"from_node": "ndvi_calculation"},
    "reducer": {"process_id": "mean"},
    "dimension": "temporal"
  }
}
# Produces temporal mean NDVI (collapses time dimension)
```

## Code Examples

### Working with Temporal RasterStacks

```python
# Create a time-aware LazyRasterStack
from titiler.openeo.processes.implementations.data_model import LazyRasterStack

# Tasks with temporal metadata
tasks = [
    (load_task, {"id": "s2_20230101", "datetime": datetime(2023, 1, 1)}),
    (load_task, {"id": "s2_20230115", "datetime": datetime(2023, 1, 15)}),
]

raster_stack = LazyRasterStack(
    tasks=tasks,
    key_fn=lambda asset: asset["id"],
    timestamp_fn=lambda asset: asset["datetime"]  # Enable temporal features
)

# Access by time
january_data = raster_stack.get_by_timestamp(datetime(2023, 1, 1))

# Temporal iteration
for timestamp in raster_stack.timestamps():
    temporal_group = raster_stack.get_by_timestamp(timestamp)
    print(f"Time {timestamp}: {len(temporal_group)} observations")
```

### Multi-band Processing

```python
# Access spectral bands within temporal observations
observation = raster_stack["s2_20230101"]  # Multi-band ImageData
bands = observation.band_names  # ["B02", "B03", "B04", "B08"]
nir_band = observation.array[3]  # NIR band (B08)
red_band = observation.array[2]  # Red band (B04)

# Calculate NDVI for this time point
ndvi = (nir_band - red_band) / (nir_band + red_band)
```

### Utility Functions

```python
from titiler.openeo.processes.implementations.data_model import to_raster_stack

# Convert single ImageData to temporal RasterStack format
img_data = ImageData(...)
raster_stack = to_raster_stack(img_data)  # {"data": img_data}

# Efficient access to temporal extremes
first_observation = get_first_item(raster_stack)  # Earliest in time
last_observation = get_last_item(raster_stack)    # Latest in time
```

## Performance Benefits

The enhanced LazyRasterStack implementation provides significant performance improvements:

### Concurrent Execution

- **Parallel Loading**: ThreadPoolExecutor enables concurrent data loading within timestamp groups
- **Configurable Workers**: Adjustable `max_workers` parameter for optimal resource utilization
- **Timestamp Grouping**: Efficient parallel processing of observations at the same time point

### Memory Optimization

- **Lazy Evaluation**: Only loads data when explicitly accessed or processed
- **Per-key Caching**: Intelligent caching prevents redundant task execution
- **Selective Loading**: Timestamp-based access loads only relevant temporal subsets

### Computational Efficiency

- **Early Termination**: Pixel selection and reduction operations can stop early when sufficient data is found
- **Temporal Ordering**: Pre-sorted temporal access eliminates runtime sorting overhead
- **Exception Resilience**: Graceful handling of failed tasks without blocking entire workflows

### Scalability Improvements

1. **Large Time Series**: Efficiently handles datasets with hundreds of temporal observations
2. **Multi-band Support**: Optimized processing of high-dimensional spectral data
3. **Memory Footprint**: Reduced memory usage for large Earth Observation collections
4. **Processing Speed**: Concurrent execution significantly reduces wall-clock time

## Best Practices

When working with the enhanced RasterStack data model:

### Temporal Organization

1. **Use timestamp functions**: Always provide `timestamp_fn` for time-series data to enable temporal features
2. **Leverage temporal grouping**: Use `get_by_timestamp()` and `groupby_timestamp()` for time-based processing
3. **Respect temporal order**: Take advantage of automatic temporal sorting for chronological processing

### Performance Optimization  

4. **Configure concurrency**: Adjust `max_workers` based on your system resources and data characteristics
5. **Use dimension reduction**: Apply `reduce_dimension()` to collapse unnecessary dimensions early in processing
6. **Employ early termination**: Use pixel selection methods that can terminate early ("first", "mean") when possible

### Multi-dimensional Processing

7. **Design dimension-aware workflows**: Structure processes to operate on appropriate dimensions (temporal vs spectral)
8. **Maintain dimensional consistency**: Ensure operations preserve or appropriately transform dimensional structure
9. **Use utility functions**: Leverage `get_first_item()`, `get_last_item()`, and `to_raster_stack()` for consistent handling

### Error Handling and Resilience

10. **Configure exception handling**: Set appropriate `allowed_exceptions` for robust data loading
11. **Handle temporal gaps**: Design workflows that gracefully handle missing temporal observations
12. **Test with diverse data**: Validate performance with various temporal resolutions and band combinations
