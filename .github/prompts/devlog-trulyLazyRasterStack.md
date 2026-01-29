# Devlog: Make LazyRasterStack Truly Lazy

## Overview

Refactoring `LazyRasterStack` to defer task execution until pixel data is actually needed, by pre-computing cutline masks from STAC item geometry metadata.

## Progress Tracker

### ✅ Step 1: Create `ImageRef` protocol/dataclass (COMPLETED)

**File:** `titiler/openeo/processes/implementations/data_model.py`

Added:

- `compute_cutline_mask()` utility function - computes cutline mask from geometry without needing ImageData
- `ImageRef` protocol - defines interface with properties: `key`, `geometry`, `width`, `height`, `bounds`, `crs`, `band_names`, `count` and methods `cutline_mask()`, `realize()`
- `LazyImageRef` dataclass - implements `ImageRef` protocol with lazy evaluation

**New imports added:**

```python
from abc import abstractmethod
from dataclasses import dataclass, field
import numpy as np
from affine import Affine
from rasterio.crs import CRS
from rasterio.features import rasterize
from rasterio.transform import from_bounds
from rasterio.warp import transform_geom
from rio_tiler.types import BBox
```

**Tests:** All 20 tests in `test_lazy_raster_stack.py` pass

### ✅ Step 2: Update `LazyRasterStack.__init__` (COMPLETED)

**File:** `titiler/openeo/processes/implementations/data_model.py`

Updated `LazyRasterStack.__init__` to accept:

- `width: Optional[int]` - output width in pixels
- `height: Optional[int]` - output height in pixels  
- `bounds: Optional[BBox]` - output bounds
- `dst_crs: Optional[CRS]` - destination CRS
- `band_names: Optional[List[str]]` - list of band names
- `band_count: Optional[int]` - number of bands

Extended `_compute_metadata()` to:

- Extract geometry from `asset.get("geometry")`
- Create `LazyImageRef` instances stored in `_image_refs: Dict[str, LazyImageRef]`

Added new methods:

- `get_image_ref(key: str) -> Optional[LazyImageRef]` - get LazyImageRef without executing task
- `get_all_image_refs() -> List[LazyImageRef]` - get all LazyImageRef instances in temporal order

**Tests:** All tests pass

### ✅ Step 3: Add `compute_cutline_mask` utility (COMPLETED)

**File:** `titiler/openeo/processes/implementations/data_model.py`

Already completed as part of Step 1. The function:

- Takes geometry, width, height, bounds, dst_crs
- Transforms geometry if CRS differs from WGS84
- Uses `rasterio.transform.from_bounds()` to compute affine transform
- Uses `rasterio.features.rasterize()` to create mask

### ✅ Step 4: Refactor `_collect_images_from_data` (COMPLETED)

**File:** `titiler/openeo/processes/implementations/reduce.py`

Updated:

- Added imports: `LazyImageRef`, `LazyRasterStack` from data_model
- Changed return type to `List[Tuple[str, Union[LazyImageRef, ImageData]]]`
- For `LazyRasterStack` with image refs, returns `LazyImageRef` instances via `data.get_image_refs()`
- Falls back to timestamp-based or regular dict iteration for other cases

**Tests:** All 20 tests pass

### ✅ Step 5: Refactor `apply_pixel_selection` (COMPLETED)

**File:** `titiler/openeo/processes/implementations/reduce.py`

Updated `apply_pixel_selection` to:

- Compute aggregated cutline mask from `LazyImageRef.cutline_mask()` without task execution
- Initialize pixsel_method metadata from first item (works with both LazyImageRef and ImageData)
- Only call `ref.realize()` when actually feeding pixels to pixel selection
- Early termination still works correctly

**Tests:** All 54 tests pass (dimension_reduction + cutline_mask)

### 🔄 Step 6: Remove `RasterStack` type definition (DEFERRED)

**File:** `titiler/openeo/processes/implementations/data_model.py`

**Decision:** Deferring this step because:

1. The rename involves 100+ usages across the codebase
2. Tests are passing with current implementation
3. The core lazy functionality is complete
4. Rename can be done as a separate PR to minimize risk

**Current status:** `LazyRasterStack` now has full lazy behavior with `ImageRef` support.
The `RasterStack = Dict[str, ImageData]` type alias remains for backwards compatibility.

### ⏳ Step 7: Remove deprecated `load_collection_and_reduce` (DEFERRED)

**File:** `titiler/openeo/stacapi.py`

**Decision:** Deferring because:

1. Function is already marked as deprecated with a warning
2. Still used in production config files (copernicus.json, eoapi.json)
3. Removing it would break existing deployments
4. Should be removed in a separate PR with migration guidance

---

## Current State (as of conversation break)

### Files Modified

1. `titiler/openeo/processes/implementations/data_model.py` - Steps 1, 2, 3 complete

### Files To Modify

1. `titiler/openeo/processes/implementations/reduce.py` - Steps 4, 5
2. `titiler/openeo/reader.py` - Remove `_apply_cutline_mask` (consolidate into data_model)
3. `titiler/openeo/stacapi.py` - Step 7, plus update calls to LazyRasterStack with new params

### Key Code Locations

- `LazyRasterStack` class: `data_model.py` lines ~340-600
- `_collect_images_from_data`: `reduce.py` lines ~175-202
- `apply_pixel_selection`: `reduce.py` lines ~225-295
- `_apply_cutline_mask`: `reader.py` lines ~668-710

### Test Commands

```bash
# Run LazyRasterStack tests
uv run pytest tests/test_lazy_raster_stack.py -v

# Run all tests
uv run pytest tests/ -v

# Quick import test
uv run python -c "from titiler.openeo.processes.implementations.data_model import ImageRef, LazyImageRef, compute_cutline_mask, LazyRasterStack; print('OK')"
```

---

## Resume Instructions

1. Check current state of files with `git status` and `git diff`
2. Verify tests still pass: `uv run pytest tests/test_lazy_raster_stack.py -v`
3. Continue from the step marked "IN PROGRESS" above
4. Update this devlog as steps are completed
