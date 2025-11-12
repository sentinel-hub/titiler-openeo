# RasterStack Data Model

In titiler-openeo, the RasterStack data model is central to how raster data is represented and processed throughout the system. This document explains the RasterStack concept, its implementation, and the performance benefits it provides.

## Overview

The RasterStack is a dictionary-like structure that maps names or dates to `ImageData` objects, allowing for consistent handling of multiple raster layers. This approach simplifies the processing of Earth Observation data by providing a unified interface for operations on raster data.

```python
# Example of RasterStack structure
RasterStack = {
    "2023-01-01": ImageData(...),  # First date
    "2023-01-15": ImageData(...),  # Second date
    "2023-02-01": ImageData(...),  # Third date
}
```

## ImageData vs RasterStack

- **ImageData**: Single raster layer, with dimensions, bounds, CRS, and metadata
- **RasterStack**: Collection of named ImageData objects, typically representing different dates or bands

## LazyRasterStack

The LazyRasterStack extends the basic RasterStack concept by implementing lazy loading of data:

```python
# LazyRasterStack only loads data when accessed
raster_stack = LazyRasterStack(tasks, date_name_fn)

# Data is only loaded when accessed
image_data = raster_stack["2023-01-01"]  # This triggers loading
```

### Key Features of LazyRasterStack

1. **On-demand Loading**: Data is loaded only when actually accessed, reducing memory usage for large collections
2. **Task-based Execution**: Uses rio-tiler's task system to efficiently process data
3. **Exception Handling**: Gracefully handles common exceptions like TileOutsideBounds
4. **Dictionary Interface**: Maintains the familiar dictionary interface for easy integration

## Advantages of the RasterStack Model

- **Consistency**: All processes now use a consistent data structure
- **Performance**: LazyRasterStack reduces memory footprint and improves performance
- **Predictability**: Standardized input/output for all operations
- **Flexibility**: Works well with time series and multi-band data

## How Processing Works with RasterStack

### Load Phase
Data is loaded from collections into a LazyRasterStack structure:

```python
# Process graph example
{
  "process_id": "load_collection",
  "arguments": {
    "id": "sentinel-2-l2a",
    "spatial_extent": {...},
    "temporal_extent": ["2023-01-01", "2023-03-01"],
    "bands": ["B04", "B08"]
  }
}
```

### Process Phase
Operations are applied uniformly to items in the RasterStack:

```python
# Process graph example to calculate NDVI
{
  "process_id": "normalized_difference",
  "arguments": {
    "x": {"from_node": "load_collection", "band": "B08"},
    "y": {"from_node": "load_collection", "band": "B04"}
  }
}
```

### Output Phase
Results are rendered as a single image or maintained as a RasterStack:

```python
# Process graph example to save result
{
  "process_id": "save_result",
  "arguments": {
    "data": {"from_node": "normalized_difference"},
    "format": "png"
  }
}
```

## Code Examples

Handling a RasterStack with basic operations:

```python
# Convert a single ImageData to a RasterStack
from titiler.openeo.processes.implementations.data_model import to_raster_stack

img_data = ImageData(...)
raster_stack = to_raster_stack(img_data)  # {"data": img_data}

# Process each image in a RasterStack consistently
def apply_to_raster_stack(raster_stack, func):
    """Apply a function to each ImageData in a RasterStack"""
    return {k: func(v) for k, v in raster_stack.items()}
```

## Performance Benefits

The LazyRasterStack implementation provides several performance benefits:

1. **Memory Efficiency**: Only loads data that is actually used
2. **Computation Efficiency**: Defers expensive computations until needed
3. **Error Resilience**: Handles exceptions during computation without failing the entire process
4. **Scalability**: Better handles large datasets with many dates/bands

## Best Practices

When working with the RasterStack data model:

1. Use `to_raster_stack()` to ensure consistent handling of both single images and collections
2. Prefer using LazyRasterStack for large collections
3. Design processes to operate on RasterStack inputs and produce RasterStack outputs
4. Use the dictionary interface (keys, values, items) for flexible processing
