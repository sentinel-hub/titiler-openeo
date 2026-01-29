# Plan: Make LazyRasterStack Truly Lazy (v2 - Updated)

Refactor `LazyRasterStack` to defer task execution until pixel data is actually needed, by pre-computing cutline masks from STAC item geometry metadata. All output dimensions and metadata will be passed explicitly at construction time. `LazyRasterStack` will replace `RasterStack` type alias so that all raster stacks are lazy by default.

## Steps

### Phase 1: Core Lazy Infrastructure (Steps 1-5) ✅ COMPLETED

1. ✅ **Create `ImageRef` protocol/dataclass** in [data_model.py](titiler/openeo/processes/implementations/data_model.py)
   - Properties: `key`, `geometry`, `width`, `height`, `bounds`, `crs`, `band_names`, `count`
   - Methods: `cutline_mask() -> numpy.ndarray`, `realize() -> ImageData`

2. ✅ **Update `LazyRasterStack.__init__`**
   - Accept explicit `width`, `height`, `dst_crs`, `bounds`, `band_names` parameters
   - Extract geometry from `asset["geometry"]` and create `LazyImageRef` instances

3. ✅ **Add `compute_cutline_mask` utility function**
   - Rasterizes geometry to mask using `rasterio.transform.from_bounds()`

4. ✅ **Refactor `_collect_images_from_data`** in [reduce.py](titiler/openeo/processes/implementations/reduce.py)
   - Return `List[Tuple[str, Union[LazyImageRef, ImageData]]]`
   - For `LazyRasterStack` with image refs, return refs without executing tasks

5. ✅ **Refactor `apply_pixel_selection`** in [reduce.py](titiler/openeo/processes/implementations/reduce.py)
   - Compute aggregated cutline using `ref.cutline_mask()` without task execution
   - Only call `ref.realize()` when feeding pixels

### Phase 2: Unify RasterStack Types (Step 6) - REQUIRES DETAILED WORK

**Current State:**

- `RasterStack = Dict[str, ImageData]` is a type alias (100+ usages)
- `LazyRasterStack(Dict[str, ImageData])` is the actual class with lazy behavior
- Many places create `{"key": ImageData(...)}` dict literals directly

**Goal:** Replace type alias with actual class so all stacks are lazy by default.

#### Sub-Step 6.1: Add factory methods to `LazyRasterStack`

**File:** `data_model.py`

```python
@classmethod
def from_dict(cls, data: Dict[str, ImageData]) -> "LazyRasterStack":
    """Create LazyRasterStack from a dictionary of ImageData."""

@classmethod  
def from_single(cls, key: str, image: ImageData) -> "LazyRasterStack":
    """Create LazyRasterStack with a single image."""
```

#### Sub-Step 6.2: Update `stacapi.py`

**File:** `stacapi.py`

- Pass new parameters (width, height, bounds, dst_crs, band_names) to `LazyRasterStack()`
- Update `_process_spatial_extent()` return type

#### Sub-Step 6.3: Update process implementations

**Files:** `apply.py`, `arrays.py`, `dem.py`, `image.py`, `indices.py`, `io.py`, `spatial.py`

- Replace dict literal returns with `LazyRasterStack.from_dict()` or `from_single()`
- Update type hints from `RasterStack` to `LazyRasterStack`

#### Sub-Step 6.4: Update `core.py` type validation

**File:** `core.py`

- Update `isinstance` checks: `isinstance(value, (dict, LazyRasterStack))` → `isinstance(value, LazyRasterStack)`
- Update type name mapping for openEO types

#### Sub-Step 6.5: Update tests

**Files:** 15+ test files

- Replace `{"key": ImageData(...)}` with `LazyRasterStack.from_single("key", ImageData(...))`
- Update `isinstance` assertions

#### Sub-Step 6.6: Final rename

**File:** `data_model.py` and all imports

- Rename `LazyRasterStack` → `RasterStack`
- Keep `LazyRasterStack` as deprecated alias (optional)
- Update all imports across codebase

### Phase 3: Cleanup (Steps 7-8)

7. ⏳ **Remove deprecated `load_collection_and_reduce`** in stacapi.py
   - Currently still used in production configs
   - Should be removed in separate PR with migration guidance

8. 🔲 **Remove `_apply_cutline_mask` from reader.py**
   - Already consolidated into `compute_cutline_mask` in data_model.py
   - Keep for backwards compat with external code or remove

## Files Affected by Step 6

| File | Type | Changes |
|------|------|---------|
| `data_model.py` | Core | Remove alias, add factory methods, rename class |
| `reduce.py` | Core | Update imports and type hints |
| `stacapi.py` | Core | Add new params, update return types |
| `core.py` | Core | Update isinstance checks, type mapping |
| `apply.py` | Process | 10+ type hint updates |
| `arrays.py` | Process | Import updates |
| `dem.py` | Process | Type hint updates |
| `image.py` | Process | Type hint updates |
| `indices.py` | Process | Type hints, dict creation |
| `io.py` | Process | Type hints, dict creation |
| `spatial.py` | Process | Type hint updates |
| `__init__.py` | Export | Update exports |
| `test_*.py` | Tests | 15+ files, fixtures, assertions |

## Key Challenges

1. **Dict Literal Compatibility**: Many places create `{"key": ImageData(...)}` directly.
   - Solution: Factory method `RasterStack.from_dict()` or `from_single()`

2. **isinstance Checks**: Code uses `isinstance(data, dict)` for RasterStack detection.
   - `LazyRasterStack` inherits from `Dict`, so this works for the class
   - But explicit dict literals won't be detected as `RasterStack`
   - Solution: Add `isinstance(data, (dict, LazyRasterStack))` or migrate to only class

3. **Return Types**: Functions return `Dict[str, ImageData]` literals.
   - Solution: Wrap returns in `RasterStack.from_dict()`

4. **Backwards Compatibility**: External code may depend on dict behavior.
   - `LazyRasterStack` already inherits from `Dict`, so dict operations work
   - May need deprecation period for type alias

## Testing Commands

```bash
# Run LazyRasterStack tests
uv run pytest tests/test_lazy_raster_stack.py -v

# Run all tests
uv run pytest tests/ -v --tb=short

# Quick import test
uv run python -c "from titiler.openeo.processes.implementations.data_model import ImageRef, LazyImageRef, compute_cutline_mask, LazyRasterStack; print('OK')"
```

## General Instructions

- Validate each sub-step with commits before proceeding
- Ensure thorough testing at each stage
- Use `uv` for all Python commands
- Update devlog as steps are completed
