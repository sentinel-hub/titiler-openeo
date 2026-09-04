# RasterStack Data Model

In titiler-openeo, the `RasterStack` class is the foundational data structure for handling Earth Observation datasets. This document explains the RasterStack architecture, its lazy loading capabilities, and the performance benefits it provides.

## Overview

`RasterStack` is a class that organizes raster data along multiple dimensions, primarily **time** and **spectral bands**. Each entry contains an `ImageData` object representing a multi-band image at a specific time point. The class inherits from `Dict[datetime, ImageData]` but adds lazy loading, temporal awareness, and intelligent caching.

**Key architectural principle**: When `load_collection` creates a RasterStack, it groups items by timestamp and merges overlapping tiles using `mosaic_reader`. This guarantees **one entry per timestamp** - all spatial tiles from the same acquisition are already mosaicked together.

```python
from titiler.openeo.processes.implementations.data_model import RasterStack
from datetime import datetime

# RasterStack behaves like a dict but with lazy loading
# Keys are datetime objects directly - no separate key_fn needed
raster_stack = RasterStack(
    tasks=tasks,
    timestamp_fn=lambda asset: asset["datetime"],  # Returns datetime, used as key
)

# Access by datetime key triggers lazy loading
dt = datetime(2023, 1, 1)
first_image = raster_stack[dt]  # Loads data on first access
```

## Dimensional Model

The RasterStack defines a clear dimensional hierarchy:

1. **Time Dimension (Primary)**: RasterStack keys represent temporal organization
2. **Spectral Dimension (Secondary)**: Each ImageData contains multiple bands
3. **Spatial Dimensions**: Each band contains 2D spatial data (height, width)

```python
# Time dimension: multiple temporal observations (datetime keys)
from datetime import datetime

temporal_stack = {
    datetime(2023, 1, 1): ImageData(array.shape=(4, 512, 512)),  # 4 bands
    datetime(2023, 2, 1): ImageData(array.shape=(4, 512, 512)),  # 4 bands
}

# Each ImageData represents multi-band observations at one time point
single_observation = temporal_stack[datetime(2023, 1, 1)]
# single_observation.array.shape = (bands, height, width) = (4, 512, 512)
```

## ImageData vs RasterStack

- **ImageData**: Multi-band raster data for a single time point with spatial extent, CRS, and band metadata
- **RasterStack**: Time-organized collection of ImageData objects with lazy loading, enabling temporal analysis and processing

## RasterStack with Temporal Intelligence

`RasterStack` provides sophisticated time-aware lazy loading and concurrent execution capabilities:

```python
from titiler.openeo.processes.implementations.data_model import RasterStack

# RasterStack with datetime keys (timestamp IS the key)
raster_stack = RasterStack(
    tasks=tasks,
    timestamp_fn=lambda asset: asset["datetime"],  # Returns datetime, used as key
    max_workers=5  # Concurrent execution
)

# Temporal access - keys ARE timestamps (datetime objects)
all_timestamps = raster_stack.timestamps()  # Sorted list of datetime keys
first_timestamp = next(iter(raster_stack.keys()))  # First datetime key
```

### Key Features of RasterStack

1. **Temporal Organization**: Automatic sorting by timestamps for time-series analysis (one item per timestamp)
2. **Concurrent Execution**: Parallel loading of data using ThreadPoolExecutor for improved performance
3. **Datetime Keys**: Keys are `datetime` objects directly - no separate key/timestamp mapping needed
4. **Intelligent Caching**: Per-key caching to avoid redundant computations
5. **Lazy Evaluation**: Data loaded only when accessed, reducing memory footprint
6. **Multi-band Support**: Each temporal observation can contain multiple spectral bands

### Temporal Processing Capabilities

```python
# Get sorted list of all timestamps (keys ARE timestamps)
for timestamp in raster_stack.timestamps():
    print(f"Available observation at: {timestamp}")
    
# Process data chronologically - keys are datetime objects
for dt_key in raster_stack.keys():  # Already in temporal order
    item = raster_stack[dt_key]
    print(f"Processing observation from {dt_key}")
    
# Efficient time-series operations via first/last properties
first_observation = raster_stack.first  # Earliest observation
last_observation = raster_stack.last    # Latest observation
```

## Advantages of the RasterStack Model

- **Temporal Consistency**: Standardized time-first organization for all Earth Observation workflows
- **Multi-dimensional Support**: Explicit handling of time and spectral dimensions
- **Concurrent Performance**: Parallel data loading reduces processing time for large datasets
- **Memory Efficiency**: RasterStack with intelligent caching minimizes memory usage
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

### Load Phase - Temporal Organization with Per-Timestamp Mosaic

When `load_collection` retrieves satellite imagery, it automatically groups STAC items by their acquisition timestamp and merges overlapping tiles using `mosaic_reader`. This means:

- **One entry per timestamp**: Each timestamp in the RasterStack contains a single merged `ImageData`, even if multiple tiles cover the area
- **Automatic tile merging**: Overlapping tiles from the same acquisition are mosaicked together
- **No duplicate timestamps**: The RasterStack is guaranteed to have unique timestamps

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
# Results in RasterStack with one entry per acquisition date
# Multiple tiles from the same date are mosaicked into a single ImageData
```

This design simplifies temporal processing since each key represents a unique moment in time with all spatial tiles already merged.

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
# Create a time-aware RasterStack
from titiler.openeo.processes.implementations.data_model import RasterStack
from datetime import datetime

# Tasks with temporal metadata
tasks = [
    (load_task, {"id": "s2_20230101", "datetime": datetime(2023, 1, 1)}),
    (load_task, {"id": "s2_20230115", "datetime": datetime(2023, 1, 15)}),
]

# timestamp_fn returns datetime, which IS used as the key
raster_stack = RasterStack(
    tasks=tasks,
    timestamp_fn=lambda asset: asset["datetime"]
)

# Access by datetime key (keys are ordered by timestamp)
first_item = raster_stack.first  # First observation
last_item = raster_stack.last    # Last observation

# Temporal iteration (keys are datetime objects, already in temporal order)
for dt_key in raster_stack.keys():
    print(f"Time {dt_key}")
```

### Multi-band Processing

```python
# Access spectral bands within temporal observations
dt = datetime(2023, 1, 1)
observation = raster_stack[dt]  # Multi-band ImageData
bands = observation.band_descriptions  # ["B02", "B03", "B04", "B08"]
nir_band = observation.array[3]  # NIR band (B08)
red_band = observation.array[2]  # Red band (B04)

# Calculate NDVI for this time point
ndvi = (nir_band - red_band) / (nir_band + red_band)
```

### Factory Methods

```python
from titiler.openeo.processes.implementations.data_model import RasterStack
from datetime import datetime

# Create RasterStack from pre-loaded images (datetime keys)
images = {
    datetime(2023, 1, 1): ImageData(...),
    datetime(2023, 1, 15): ImageData(...),
}
raster_stack = RasterStack.from_images(images)

# Access first and last images
first_observation = raster_stack.first  # Earliest in time
last_observation = raster_stack.last    # Latest in time
```

## Performance Benefits

The `RasterStack` implementation provides significant performance improvements:

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

When working with the RasterStack data model:

### Temporal Organization

1. **Use timestamp functions**: Always provide `timestamp_fn` for time-series data - the returned datetime IS the key
2. **Leverage temporal ordering**: Keys (datetime objects) are automatically sorted for chronological processing
3. **Use first/last properties**: Access `.first` and `.last` for efficient endpoint access

### Performance Optimization  

4. **Configure concurrency**: Adjust `max_workers` based on your system resources and data characteristics
5. **Use dimension reduction**: Apply `reduce_dimension()` to collapse unnecessary dimensions early in processing
6. **Employ early termination**: Use pixel selection methods that can terminate early ("first", "mean") when possible

### Multi-dimensional Processing

7. **Design dimension-aware workflows**: Structure processes to operate on appropriate dimensions (temporal vs spectral)
8. **Maintain dimensional consistency**: Ensure operations preserve or appropriately transform dimensional structure
9. **Use factory methods**: Leverage `RasterStack.from_images()` and `.first`/`.last` properties for consistent handling

### Error Handling and Resilience

10. **Configure exception handling**: Set appropriate `allowed_exceptions` for robust data loading
11. **Handle temporal gaps**: Design workflows that gracefully handle missing temporal observations
12. **Test with diverse data**: Validate performance with various temporal resolutions and band combinations
