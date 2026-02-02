# RasterStack Migration Guide

This guide helps you migrate from the old `LazyRasterStack` / type alias architecture to the new unified `RasterStack` class.

## Summary of Changes

The RasterStack architecture has been simplified:

| Before | After |
|--------|-------|
| `RasterStack = Dict[str, ImageData]` (type alias) | `RasterStack` (class) |
| `LazyRasterStack` (separate class) | `RasterStack` (unified) |
| `LazyImageRef` / `EagerImageRef` (two classes) | `ImageRef` (unified) |
| `get_first_item()` / `get_last_item()` | `.first` / `.last` properties |
| `to_raster_stack()` | `RasterStack.from_images()` |

## Migration Steps

### 1. Update Imports

```python
# Before
from titiler.openeo.processes.implementations.data_model import (
    LazyRasterStack,
    LazyImageRef,
    to_raster_stack,
    get_first_item,
    get_last_item,
)

# After
from titiler.openeo.processes.implementations.data_model import (
    RasterStack,
    ImageRef,
)
```

### 2. Replace LazyRasterStack with RasterStack

The class interface remains the same:

```python
# Before
raster_stack = LazyRasterStack(
    tasks=tasks,
    key_fn=lambda asset: asset["id"],
    timestamp_fn=lambda asset: asset["datetime"],
)

# After
raster_stack = RasterStack(
    tasks=tasks,
    key_fn=lambda asset: asset["id"],
    timestamp_fn=lambda asset: asset["datetime"],
)
```

### 3. Replace to_raster_stack() with Factory Methods

```python
# Before
from titiler.openeo.processes.implementations.data_model import to_raster_stack

img_data = ImageData(...)
raster_stack = to_raster_stack(img_data)  # {"data": img_data}

# After
raster_stack = RasterStack.from_images({"data": img_data})
```

### 4. Replace get_first_item() / get_last_item()

```python
# Before
from titiler.openeo.processes.implementations.data_model import get_first_item, get_last_item

first = get_first_item(raster_stack)
last = get_last_item(raster_stack)

# After (using properties)
first = raster_stack.first
last = raster_stack.last

# Alternative for plain dicts in tests
first = next(iter(my_dict.values()))
```

### 5. Replace LazyImageRef with ImageRef

The unified `ImageRef` class handles both lazy and eager states:

```python
# Before (lazy)
ref = LazyImageRef(
    key="my_key",
    task_fn=lambda: load_image(),
    width=256,
    height=256,
    ...
)

# After (lazy)
ref = ImageRef.from_task(
    key="my_key",
    task_fn=lambda: load_image(),
    width=256,
    height=256,
    ...
)

# After (eager - pre-loaded image)
ref = ImageRef.from_image(key="my_key", image=my_image_data)
```

### 6. Update isinstance Checks

```python
# Before
if isinstance(data, LazyRasterStack):
    ...
elif isinstance(data, dict):
    ...

# After (single type)
if isinstance(data, RasterStack):
    ...
```

## ImageRef State Management

The new `ImageRef` class manages lazy/eager state internally:

```python
ref = ImageRef.from_task(key="my_key", task_fn=load_fn, ...)

# Check if data is loaded
if ref.realized:
    print("Data already loaded")
else:
    print("Data will load on first access")

# Access data (loads if necessary)
image = ref.realize()

# After realize(), ref.realized is True
assert ref.realized
```

## New Factory Methods

### RasterStack.from_images()

Create a RasterStack from pre-loaded ImageData:

```python
images = {
    "2023-01-01": ImageData(...),
    "2023-01-15": ImageData(...),
}
raster_stack = RasterStack.from_images(images)
```

### RasterStack.from_tasks()

Create a RasterStack from task tuples (same as constructor):

```python
tasks = [
    (load_fn1, {"id": "item1", "datetime": dt1}),
    (load_fn2, {"id": "item2", "datetime": dt2}),
]
raster_stack = RasterStack.from_tasks(
    tasks=tasks,
    key_fn=lambda a: a["id"],
    timestamp_fn=lambda a: a["datetime"],
)
```

### ImageRef.from_task()

Create a lazy ImageRef:

```python
ref = ImageRef.from_task(
    key="my_key",
    task_fn=lambda: load_image(),
    width=256,
    height=256,
    bounds=(0, 0, 1, 1),
    crs=CRS.from_epsg(4326),
    band_names=["red", "green", "blue"],
    geometry={"type": "Polygon", ...},  # Optional, for cutline mask
)
```

### ImageRef.from_image()

Create an eager ImageRef from pre-loaded data:

```python
ref = ImageRef.from_image(key="my_key", image=my_image_data)
```

## Benefits of the New Architecture

1. **Simpler mental model**: One class (`RasterStack`) instead of two
2. **No isinstance checks**: All code paths work with `RasterStack`
3. **Unified ImageRef**: Single class manages lazy/eager state internally
4. **Better factory methods**: Clear entry points for different use cases
5. **Cleaner code**: No `Union` types or multiple collection patterns

## Common Patterns

### Processing All Images

```python
# Get all image references (lazy)
for key, ref in raster_stack.get_image_refs():
    # Compute cutline mask WITHOUT loading data
    mask = ref.cutline_mask()
    
    # Load data only when needed
    image = ref.realize()
```

### Temporal Access

```python
# Get sorted timestamps\nfor timestamp in raster_stack.timestamps():
    print(f"Available: {timestamp}")

# Keys are already in temporal order
for key in raster_stack.keys():
    timestamp = raster_stack.get_timestamp(key)
    print(f"{key}: {timestamp}")

# Efficient first/last access
first_image = raster_stack.first
last_image = raster_stack.last
```

### Testing with Pre-loaded Images

```python
# For tests, use from_images() instead of plain dicts
def test_my_process():
    images = {"t1": create_test_image(), "t2": create_test_image()}
    raster_stack = RasterStack.from_images(images)
    
    result = my_process(raster_stack)
    assert result.first.array.shape == expected_shape
```
