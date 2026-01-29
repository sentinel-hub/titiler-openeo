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

### 🔄 Step 6: Remove `RasterStack` type definition (NEEDS DETAILED PLAN)

**File:** `titiler/openeo/processes/implementations/data_model.py`

**Analysis:** This is a large refactoring effort. Here's the comprehensive breakdown:

#### Current State

- `RasterStack = Dict[str, ImageData]` is a type alias used throughout the codebase
- `LazyRasterStack(Dict[str, ImageData])` is the actual class with lazy behavior
- Code uses `RasterStack` in type hints but often creates `Dict[str, ImageData]` literals
- `isinstance(data, dict)` checks are used to detect RasterStack

#### Files Requiring Changes

**1. Core Data Model (`data_model.py`)**

- Remove `RasterStack = Dict[str, ImageData]` type alias
- Rename `LazyRasterStack` → `RasterStack`  
- Update `get_first_item()`, `get_last_item()`, `to_raster_stack()` type hints
- Add factory method for creating `RasterStack` from `Dict[str, ImageData]`

**2. Process Implementations (7 files)**

- `apply.py` - 10+ usages of `RasterStack` type hint
- `arrays.py` - imports and usage
- `dem.py` - type hints
- `image.py` - type hints  
- `indices.py` - type hints
- `io.py` - type hints and dict creation
- `spatial.py` - type hints

**3. Reduce Module (`reduce.py`)**

- Already updated with `LazyRasterStack` import
- Update type hints to use new `RasterStack`

**4. STAC API (`stacapi.py`)**

- 5+ method return types
- `LazyRasterStack` instantiation (needs new params)
- Update return types to `RasterStack`

**5. Core Module (`core.py`)**

- Type checking: `isinstance(value, (dict, LazyRasterStack))`
- Type name mapping: `"LazyRasterStack", "RasterStack"`
- Need to update to new unified `RasterStack`

**6. Tests (15+ files)**

- Type hints in test fixtures
- `isinstance` checks
- Direct dict creation `{"key": ImageData(...)}`

#### Key Challenges

1. **Dict Literal Compatibility**: Many places create `{"key": ImageData(...)}` directly.
   - Need factory method: `RasterStack.from_dict({"key": img})`
   - Or update all locations to use constructor

2. **isinstance Checks**: Code checks `isinstance(data, dict)` for RasterStack detection.
   - `LazyRasterStack` inherits from `Dict`, so this still works
   - But explicit dict literals won't be `RasterStack` instances

3. **Return Types**: Functions return `Dict[str, ImageData]` literals.
   - Need to wrap in `RasterStack` or use factory method

4. **Type Validation**: `core.py` validates types for openEO.
   - Need to update type mapping logic

#### Proposed Sub-Steps for Step 6

**6.1** Add `RasterStack` as alias for `LazyRasterStack` (temporary)

```python
# Keep LazyRasterStack for backwards compat
RasterStack = LazyRasterStack
```

**6.2** Add factory methods to `RasterStack`:

```python
@classmethod
def from_dict(cls, data: Dict[str, ImageData]) -> "RasterStack":
    """Create RasterStack from a dictionary of ImageData."""

@classmethod  
def from_single(cls, key: str, image: ImageData) -> "RasterStack":
    """Create RasterStack with a single image."""
```

**6.3** Update `stacapi.py` to pass new parameters to `RasterStack`

**6.4** Update process implementations to use factory methods

**6.5** Update `core.py` type validation

**6.6** Update tests

**6.7** Remove `LazyRasterStack` name, keep only `RasterStack`

#### Decision

Split Step 6 into multiple sub-PRs:

- **PR 1 (Current)**: Steps 1-5 complete - LazyRasterStack is truly lazy
- **PR 2**: Step 6.1-6.2 - Add factory methods and alias
- **PR 3**: Step 6.3-6.6 - Update usages  
- **PR 4**: Step 6.7 - Final cleanup

### ⏳ Step 7: Remove deprecated `load_collection_and_reduce` (DEFERRED)

**File:** `titiler/openeo/stacapi.py`

**Decision:** Deferring because:

1. Function is already marked as deprecated with a warning
2. Still used in production config files (copernicus.json, eoapi.json)
3. Removing it would break existing deployments
4. Should be removed in a separate PR with migration guidance

---

## Current State (Updated January 29, 2026)

### Phase 1 Complete ✅

Steps 1-5 are complete. The core lazy infrastructure is in place:

- `ImageRef` protocol and `LazyImageRef` dataclass work correctly
- `LazyRasterStack` accepts new dimension parameters
- `compute_cutline_mask()` can compute masks without loading pixel data
- `_collect_images_from_data()` returns `LazyImageRef` instances when available
- `apply_pixel_selection()` defers task execution until pixel feeding

### Files Modified

1. `titiler/openeo/processes/implementations/data_model.py` - Steps 1, 2, 3 complete
2. `titiler/openeo/processes/implementations/reduce.py` - Steps 4, 5 complete

### Files Still To Modify (Phase 2 - Step 6)

See [plan-trulyLazyRasterStack-v2.prompt.md](plan-trulyLazyRasterStack-v2.prompt.md) for detailed breakdown:

1. `data_model.py` - Add factory methods, rename class
2. `stacapi.py` - Pass new params, update return types
3. `apply.py`, `arrays.py`, `dem.py`, `image.py`, `indices.py`, `io.py`, `spatial.py` - Update type hints
4. `core.py` - Update type validation
5. 15+ test files - Update fixtures and assertions

### Test Results

```
uv run pytest tests/test_lazy_raster_stack.py tests/test_dimension_reduction.py -v
# Result: 43 passed ✅
```

### Key Code Locations

- `LazyRasterStack` class: `data_model.py` lines ~338-700
- `LazyImageRef` dataclass: `data_model.py` lines ~145-245
- `compute_cutline_mask()`: `data_model.py` lines ~45-90
- `_collect_images_from_data`: `reduce.py` lines ~175-215
- `apply_pixel_selection`: `reduce.py` lines ~240-330
- `_apply_cutline_mask`: `reader.py` lines ~668-710 (to be removed in cleanup)

### Test Commands

```bash
# Run LazyRasterStack tests
uv run pytest tests/test_lazy_raster_stack.py -v

# Run dimension reduction tests
uv run pytest tests/test_dimension_reduction.py -v

# Run all tests
uv run pytest tests/ -v --tb=short

# Quick import test
uv run python -c "from titiler.openeo.processes.implementations.data_model import ImageRef, LazyImageRef, compute_cutline_mask, LazyRasterStack; print('OK')"
```

---

## Resume Instructions

1. Check current state: `git status` and `git diff`
2. Verify tests pass: `uv run pytest tests/test_lazy_raster_stack.py tests/test_dimension_reduction.py -v`
3. Reference the updated plan: [plan-trulyLazyRasterStack-v2.prompt.md](plan-trulyLazyRasterStack-v2.prompt.md)
4. Continue with Step 6.1: Add factory methods to `LazyRasterStack`
5. Update this devlog as steps are completed
