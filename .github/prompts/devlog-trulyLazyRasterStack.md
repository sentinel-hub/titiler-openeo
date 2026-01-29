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

- **PR 1 (Current)**: Steps 1-5 complete + Step 6.2 (stacapi.py and io.py updates) - LazyRasterStack is truly lazy
- **PR 2**: Step 6.1 - Add factory methods and alias
- **PR 3**: Step 6.3-6.6 - Update usages  
- **PR 4**: Step 6.7 - Final cleanup

### ✅ Step 6.2: Update `stacapi.py` and `io.py` (COMPLETED)

**File:** `titiler/openeo/stacapi.py`

Updated `_process_spatial_extent()` to pass new parameters to `LazyRasterStack`:

```python
return LazyRasterStack(
    tasks=tasks,
    key_fn=lambda asset: asset["id"],
    timestamp_fn=lambda asset: _props_to_datetime(asset["properties"]),
    allowed_exceptions=(TileOutsideBounds,),
    # New parameters for truly lazy behavior
    width=int(width) if width else None,
    height=int(height) if height else None,
    bounds=tuple(bbox),
    dst_crs=crs,
    band_names=bands,
)
```

**File:** `titiler/openeo/processes/implementations/io.py`

Updated `load_url()` to extract COG metadata and pass to `LazyRasterStack`:

```python
# Get metadata from COG to set bbox, dimensions, and CRS
with COGReader(url) as cog:
    item["bbox"] = [float(x) for x in cog.bounds]
    cog_width = cog.dataset.width
    cog_height = cog.dataset.height
    cog_crs = cog.dataset.crs
    cog_bounds = tuple(float(x) for x in cog.bounds)

# Return a LazyRasterStack with new parameters
return LazyRasterStack(
    tasks=tasks,
    key_fn=lambda _: "data",
    timestamp_fn=lambda _: datetime.now(),
    allowed_exceptions=(),
    width=cog_width,
    height=cog_height,
    bounds=cog_bounds,
    dst_crs=cog_crs,
    band_names=["data"],
)
```

### ✅ New Test Coverage Added (COMPLETED)

**File:** `tests/test_truly_lazy_raster_stack.py`

Created comprehensive test suite with 15 tests covering:

- `TestComputeCutlineMask`: 4 tests for cutline mask computation
- `TestLazyImageRef`: 4 tests for LazyImageRef behavior (deferred execution, caching)
- `TestLazyRasterStackWithDimensions`: 3 tests for LazyRasterStack with dimension parameters
- `TestCollectImagesFromDataWithLazyRefs`: 1 test for lazy ref collection
- `TestApplyPixelSelectionTrulyLazy`: 3 tests for pixel selection with lazy execution

### ✅ Fixed `_collect_images_from_data` Duck Typing (COMPLETED)

**File:** `titiler/openeo/processes/implementations/reduce.py`

Updated to use duck typing for timestamp grouping:

```python
# Check if data is a LazyRasterStack with image refs (truly lazy path)
if isinstance(data, LazyRasterStack):
    image_refs = data.get_image_refs()
    if image_refs:
        return image_refs

# Fall back to timestamp-based grouping if available (duck typing)
if hasattr(data, "timestamps") and hasattr(data, "get_by_timestamp"):
    timestamps = data.timestamps()
    for timestamp in sorted(timestamps):
        timestamp_items = data.get_by_timestamp(timestamp)
        if timestamp_items:
            all_items.extend(timestamp_items.items())
    return all_items
```

### ⏳ Step 7: Remove deprecated `load_collection_and_reduce` (DEFERRED)

**File:** `titiler/openeo/stacapi.py`

**Decision:** Deferring because:

1. Function is already marked as deprecated with a warning
2. Still used in production config files (copernicus.json, eoapi.json)
3. Removing it would break existing deployments
4. Should be removed in a separate PR with migration guidance

---

## Current State (Updated January 29, 2026)

### Phase 1 Complete + Phase 2 In Progress ✅

Steps 1-5 are complete, plus significant progress on Phase 2 (simplification):

- `ImageRef` protocol and `LazyImageRef` dataclass work correctly
- `LazyRasterStack` accepts new dimension parameters
- `compute_cutline_mask()` can compute masks without loading pixel data
- `_collect_images_from_data()` returns `LazyImageRef` instances when available
- `apply_pixel_selection()` defers task execution until pixel feeding
- **Production code now passes new parameters to `LazyRasterStack`** (stacapi.py and io.py)
- **Comprehensive test coverage for truly lazy behavior** (15 new tests)

### Phase 2: Simplification Progress (v3 Plan)

Following [plan-trulyLazyRasterStack-v3.prompt.md](plan-trulyLazyRasterStack-v3.prompt.md):

#### ✅ Step 2.1: Add Factory Methods to LazyRasterStack (COMPLETED)

**File:** `titiler/openeo/processes/implementations/data_model.py`

Added:

- `from_images(cls, images: Dict[str, ImageData]) -> LazyRasterStack` - Create from pre-loaded ImageData
- `from_single(cls, key: str, image: ImageData) -> LazyRasterStack` - Create single-image stack
- `first` property - Get first item (temporal order)
- `last` property - Get last item (temporal order)

Updated:

- `get_first_item()` - Now uses `data.first` for LazyRasterStack
- `get_last_item()` - Now uses `data.last` for LazyRasterStack

#### ✅ Step 2.2: Simplify _collect_images_from_data (COMPLETED)

**File:** `titiler/openeo/processes/implementations/reduce.py`

Removed timestamp-based duck typing fallback, simplified to:

- LazyRasterStack with refs: return refs directly
- LazyRasterStack without refs: iterate and collect
- Regular dict: iterate and collect

#### ✅ Step 3.1: Update apply.py (COMPLETED)

**File:** `titiler/openeo/processes/implementations/apply.py`

Updated all dict literal returns to use factory methods:

- `apply()` - returns `LazyRasterStack.from_images(result)`
- `_apply_temporal_dimension()` - returns `LazyRasterStack.from_images()` or `from_single()`
- `_apply_spectral_dimension_stack()` - returns `LazyRasterStack.from_images(result)`
- Single-image spectral case - returns `LazyRasterStack.from_single()`

#### ✅ Step 3.2: Update image.py (COMPLETED)

**File:** `titiler/openeo/processes/implementations/image.py`

Updated:

- `image_indexes()` - returns `LazyRasterStack.from_images(result)`
- `color_formula()` - returns `LazyRasterStack.from_images(result)`
- `colormap()` - returns `LazyRasterStack.from_images(result)`

#### ✅ Step 3.3: Update indices.py (COMPLETED)

**File:** `titiler/openeo/processes/implementations/indices.py`

Updated:

- `ndwi()` - returns `LazyRasterStack.from_images(result)`
- `ndvi()` - returns `LazyRasterStack.from_images(result)`

#### ✅ Step 3.4: Update reduce.py returns (COMPLETED)

**File:** `titiler/openeo/processes/implementations/reduce.py`

Updated:

- `_create_pixel_selection_result()` - returns `LazyRasterStack.from_single("data", ImageData(...))`
- `_reduce_temporal_dimension()` - returns `LazyRasterStack.from_single("reduced", ImageData(...))`
- `_reduce_spectral_dimension_stack()` - returns `LazyRasterStack.from_images(result)`

#### ✅ Step 4: Update core.py type checks (COMPLETED)

**File:** `titiler/openeo/processes/implementations/core.py`

Updated:

- Type mapping now includes `LazyRasterStack` explicitly for "raster-cube" detection
- `isinstance(value, (dict, LazyRasterStack))` patterns retained for backward compat

### Files Modified in This Session

1. `titiler/openeo/processes/implementations/data_model.py` - Added factory methods and properties
2. `titiler/openeo/processes/implementations/reduce.py` - Simplified collection and updated returns
3. `titiler/openeo/processes/implementations/apply.py` - All returns use factory methods
4. `titiler/openeo/processes/implementations/image.py` - All returns use factory methods
5. `titiler/openeo/processes/implementations/indices.py` - All returns use factory methods
6. `titiler/openeo/processes/implementations/core.py` - Updated type checks

### Files Still To Modify

1. `arrays.py` - `to_image()` and `create_data_cube()` return dicts (special cases)
2. Various test files - Update fixtures and assertions to handle `LazyRasterStack` returns
3. Final rename `LazyRasterStack` → `RasterStack` (future PR)

### Next Steps

1. ✅ All tests pass (479 passed, 11 skipped)
2. Consider final rename `LazyRasterStack` → `RasterStack` in future PR
3. Update `arrays.py` edge cases if needed (empty dict case is special)

### Test Results

```bash
uv run pytest tests/ --ignore=tests/test_main.py -q
# Result: 479 passed, 11 skipped ✅
```

### Test Commands

```bash
# Run all tests (excluding slow main tests)
uv run pytest tests/ --ignore=tests/test_main.py -v

# Run specific test files
uv run pytest tests/test_lazy_raster_stack.py -v
uv run pytest tests/test_truly_lazy_raster_stack.py -v
uv run pytest tests/test_dimension_reduction.py -v
uv run pytest tests/test_processes.py -v
uv run pytest tests/test_timestamp_grouping.py -v

# Quick smoke test
uv run python -c "from titiler.openeo.processes.implementations.data_model import LazyRasterStack; print('OK')"
```

---

## Resume Instructions

1. Check current state: `git status` and `git diff`
2. Run tests: `uv run pytest tests/ --ignore=tests/test_main.py -q`
3. All Phase 2 simplification steps are complete
4. Consider creating PR for review
5. Future work: Rename `LazyRasterStack` → `RasterStack` (separate PR)
