```prompt
# Plan: Make LazyRasterStack Truly Lazy (v3 - Simplification Focus)

## Vision

Instead of just mechanical replacement of `RasterStack` type alias with `LazyRasterStack`, we target **simplification** of the entire data model by:

1. Identifying unnecessary complexity in the current architecture
2. Consolidating redundant abstractions  
3. Eliminating code paths that exist only for historical reasons
4. Making the "happy path" the default path

## Current Complexity Analysis

### Problem 1: Dual Type System
- `RasterStack = Dict[str, ImageData]` is a type alias (no behavior)
- `LazyRasterStack(Dict[str, ImageData])` is the actual class with lazy behavior
- Code must constantly check `isinstance(data, LazyRasterStack)` vs `isinstance(data, dict)`
- Many places create bare dicts that miss lazy evaluation benefits

**Simplification:** One class, one behavior. `LazyRasterStack` becomes the only way to represent a raster stack.

### Problem 2: Multiple Collection Patterns in `_collect_images_from_data`
```python
# Current: 3 different collection strategies
if isinstance(data, LazyRasterStack):
    image_refs = data.get_image_refs()
    if image_refs:  # Path 1: LazyImageRef
        return image_refs

if hasattr(data, "timestamps") and hasattr(data, "get_by_timestamp"):  # Path 2: timestamp
    ...

for key in data.keys():  # Path 3: fallback
    ...
```

**Simplification:** Single unified collection path - LazyRasterStack always provides image refs.

### Problem 3: Redundant Helper Functions

- `get_first_item()` - has 4 branches for different types
- `get_last_item()` - has 4 branches for different types  
- `to_raster_stack()` - conversion utility that shouldn't need to exist

**Simplification:** With unified type, these become trivial methods on the class itself.

### Problem 4: Scattered Type Checks

- `core.py`: `isinstance(value, (dict, LazyRasterStack))`
- `stacapi.py`: Type annotations mention both `RasterStack` and `LazyRasterStack`
- Every consumer must know about both types

**Simplification:** Single type = single check.

### Problem 5: Factory Pattern Missing

- Current: Create tasks manually, call `LazyRasterStack(tasks=..., key_fn=..., ...)`
- Better: `RasterStack.from_stac_items()`, `RasterStack.from_images()`, `RasterStack.from_single()`

**Simplification:** Clear entry points that handle complexity internally.

## Proposed Architecture

### Single Class: `RasterStack`

```python
class RasterStack(Dict[str, ImageData]):
    """A raster stack with lazy loading and temporal awareness.
    
    This is THE data structure for collections of raster images.
    All images share the same spatial extent and CRS.
    """
    
    # Core properties (always available)
    width: int
    height: int
    bounds: BBox
    crs: CRS
    band_names: List[str]
    
    # Lazy evaluation
    _image_refs: Dict[str, ImageRef]
    _data_cache: Dict[str, ImageData]
    
    # Temporal metadata
    _timestamps: Dict[str, datetime]
    
    # Factory methods
    @classmethod
    def from_tasks(cls, tasks, ...) -> "RasterStack": ...
    
    @classmethod  
    def from_images(cls, images: Dict[str, ImageData]) -> "RasterStack": ...
    
    # NOTE: from_single() was removed - just use from_images({"key": img}) instead
    
    # Unified access
    def get_ref(self, key: str) -> ImageRef: ...
    def get_refs(self) -> List[ImageRef]: ...
    
    # Convenience
    @property
    def first(self) -> ImageData: ...
    
    @property
    def last(self) -> ImageData: ...
```

### Simplified Collection Pattern

```python
def _collect_images_from_data(data: RasterStack) -> List[Tuple[str, ImageRef]]:
    """Single code path - always returns ImageRef instances."""
    return data.get_refs()  # That's it!
```

### Simplified Type Checks

```python
# Before (scattered everywhere)
if isinstance(data, (dict, LazyRasterStack)):
    ...

# After (one type)
if isinstance(data, RasterStack):
    ...
```

## Implementation Plan

### Phase 1: Core Lazy Infrastructure ✅ COMPLETED

1. ✅ `ImageRef` protocol and `LazyImageRef` dataclass
2. ✅ `compute_cutline_mask()` utility
3. ✅ `LazyRasterStack` with dimension parameters
4. ✅ `_collect_images_from_data()` returns LazyImageRef
5. ✅ `apply_pixel_selection()` defers execution

### Phase 2: Simplify RasterStack Class (NEW)

#### Step 2.1: Add Factory Methods

Add to `LazyRasterStack`:

```python
@classmethod
def from_images(cls, images: Dict[str, ImageData]) -> "LazyRasterStack":
    """Create from pre-loaded ImageData (wraps for consistency)."""
    # Extract common properties from first image
    first = next(iter(images.values()))
    # Create "dummy" tasks that just return the already-loaded images
    tasks = [(lambda img=img: img, {"id": k}) for k, img in images.items()]
    return cls(
        tasks=tasks,
        key_fn=lambda a: a["id"],
        width=first.width,
        height=first.height,
        bounds=first.bounds,
        dst_crs=first.crs,
        band_names=first.band_names,
    )

@classmethod
def from_single(cls, key: str, image: ImageData) -> "LazyRasterStack":
    """Create single-image stack."""
    return cls.from_images({key: image})

@property
def first(self) -> ImageData:
    """Get first item (temporal order)."""
    return self[self._keys[0]]

@property
def last(self) -> ImageData:
    """Get last item (temporal order)."""
    return self[self._keys[-1]]
```

#### Step 2.2: Always Create ImageRef

Update `_compute_metadata()` to ALWAYS create `LazyImageRef` instances, even if dimension params not passed:

- If no dimensions: derive from first task result (lazy evaluated)
- Or require dimensions at construction (breaking change, but cleaner)

#### Step 2.3: Deprecate Bare Dict Creation

Find and update all locations that create `{"key": ImageData(...)}` literals:

| Location | Current | Target |
|----------|---------|--------|
| Process returns | `return {"key": img}` | `return RasterStack.from_single("key", img)` |
| Test fixtures | `{"k": ImageData(...)}` | `RasterStack.from_single("k", ...)` |
| apply.py | `{k: _process_img(img) for ...}` | `RasterStack.from_images({...})` |

### Phase 3: Remove Helper Functions ✅ COMPLETED

#### Step 3.1: Replace `get_first_item()` / `get_last_item()` ✅

~~Current signature: `get_first_item(data: Union[ImageData, RasterStack]) -> ImageData`~~

~~Options:~~
~~1. **Keep as utility** but simplify implementation (just call `data.first`)~~
~~2. **Remove entirely** - callers use `data.first` or handle ImageData separately~~

**Decision:** Removed. Callers now use:

- `data.first` for LazyRasterStack
- `next(iter(data.values()))` as fallback for plain dicts in tests

#### Step 3.2: Remove `to_raster_stack()` ✅

~~This function only exists because we have two types. With unified type, it's unnecessary.~~

**Decision:** Removed. Callers now use `LazyRasterStack.from_images({"data": img})` instead.

### Phase 4: Rename and Cleanup ✅ COMPLETED

#### Step 4.1: Rename `LazyRasterStack` → `RasterStack` ✅

```python
# data_model.py
class RasterStack(Dict[str, ImageData]):
    \"\"\"Unified raster stack class.\"\"\"
    ...

# Keep for backwards compatibility (one release cycle)
LazyRasterStack = RasterStack  # Deprecated alias
```

#### Step 4.2: Update All Imports ✅

All files that imported `LazyRasterStack` now import `RasterStack`.

#### Step 4.3: Remove Type Alias ✅

```python
# DELETED THIS LINE
RasterStack = Dict[str, ImageData]  # <- GONE
```

### Phase 5: Simplify Collection Patterns ✅ COMPLETED

#### Step 5.1: Simplify `_collect_images_from_data()` ✅

```python
def _collect_images_from_data(data: RasterStack) -> List[Tuple[str, ImageRef]]:
    """Collect all image references from a RasterStack."""
    # RasterStack with image refs - truly lazy path
    if isinstance(data, RasterStack):
        image_refs = data.get_image_refs()
        if image_refs:
            return image_refs

    # RasterStack without refs - return actual ImageData
    result = []
    for key in data.keys():
        try:
            result.append((key, data[key]))
        except KeyError:
            continue
    return result
```

#### Step 5.2: Update All Tests to Use RasterStack.from_images() ✅

Tests in `test_cutline_mask.py`, `test_pixel_selection_reducers.py`, `test_reduce_processes.py`, `test_dimension_reduction.py`, and `test_truly_lazy_raster_stack.py` updated to use `RasterStack.from_images()` instead of plain dicts.

### Phase 6: Unified ImageRef Class ✅ COMPLETED

**Goal:** Single `ImageRef` class that manages both lazy and eager states internally - no multiple classes, no isinstance checks, NO BACKWARDS COMPATIBILITY ALIASES.

#### Solution: Unified ImageRef with State Management

ONE `ImageRef` class (no `LazyImageRef`, no `EagerImageRef`):

- `realized` property tracks whether data has been loaded
- `from_task()` factory creates lazy refs (task function, no cached image)
- `from_image()` factory creates eager refs (pre-loaded image, no task)
- `realize()` loads data on first call (lazy) or returns cached image (eager)
- `cutline_mask()` computes from geometry (if available) or returns image's mask

```python
@dataclass
class ImageRef:
    """A unified image reference that manages lazy or eager data access."""
    _key: str
    _width: int
    _height: int
    _bounds: BBox
    _crs: Optional[CRS]
    _band_names: List[str]
    _count: int
    _geometry: Optional[Dict[str, Any]] = None
    _task_fn: Optional[Callable[[], ImageData]] = None  # For lazy
    _image: Optional[ImageData] = None  # For eager/cached
    
    @property
    def realized(self) -> bool:
        return self._image is not None
    
    def realize(self) -> ImageData:
        if self._image is None:
            self._image = self._task_fn()
        return self._image
    
    @classmethod
    def from_task(cls, key, task_fn, ...) -> "ImageRef": ...
    
    @classmethod
    def from_image(cls, key, image: ImageData) -> "ImageRef": ...
```

#### Step 6.1: Unified ImageRef Class ✅

- Single class with `_task_fn` (lazy) and `_image` (eager/cached) fields
- `realized` property for state inspection
- Factory methods `from_task()` and `from_image()` for construction
- **NO** backwards compatibility aliases

#### Step 6.2: Simplified _collect_images_from_data() ✅

```python
def _collect_images_from_data(data: RasterStack) -> List[Tuple[str, ImageRef]]:
    """Always returns ImageRef instances."""
    image_refs = data.get_image_refs()
    if image_refs:
        return image_refs
    # Fallback: create ImageRef from pre-loaded images
    return [(key, ImageRef.from_image(key=key, image=data[key])) for key in data.keys()]
```

#### Step 6.3: Remove isinstance Checks ✅

All consumers now use uniform interface:

```python
for key, ref in all_items:
    cutline_masks.append(ref.cutline_mask())  # Always works!
    img = ref.realize()  # Always works!
```

### Phase 7: Documentation and Migration Guide ✅ COMPLETED

#### Step 7.1: Update Docstrings ✅

- `RasterStack` class docstring updated to reflect it's a class, not a type alias
- `ImageRef` class docstring documents the unified lazy/eager state management
- All comments referencing `LazyRasterStack` updated to `RasterStack`

#### Step 7.2: Create Migration Guide ✅

Created `docs/src/migration-rasterstack.md` with:

- Summary of changes (old vs new)
- Step-by-step migration instructions
- Code examples for all new patterns
- Factory method documentation

#### Step 7.3: Update Existing Documentation ✅

Updated `docs/src/raster-stack.md`:

- Replaced all `LazyRasterStack` references with `RasterStack`
- Updated code examples to use factory methods
- Removed references to deprecated helper functions
- Added migration guide to mkdocs navigation

## Files Affected

### Core Changes (Phase 2-4)

| File | Changes |
|------|---------|
| `data_model.py` | Add factory methods, rename class, remove alias |
| `reduce.py` | Simplify `_collect_images_from_data()` |
| `core.py` | Simplify type checks |
| `stacapi.py` | Update return types |
| `io.py` | Use factory methods |

### Process Updates (Phase 3)

| File | Changes |
|------|---------|
| `apply.py` | Use `RasterStack.from_images()` for returns |
| `arrays.py` | Import updates |
| `dem.py` | Return type updates |
| `image.py` | Return type updates |
| `indices.py` | Return type updates |
| `spatial.py` | Return type updates |

### Test Updates

| File | Changes |
|------|---------|
| All test files | Use factory methods instead of bare dicts |

## Success Metrics

After completion:

1. **Zero** locations create `{"key": ImageData(...)}` literals
2. **Zero** uses of `isinstance(data, dict)` for RasterStack detection
3. **Zero** helper functions that handle `Union[ImageData, RasterStack]`
4. **One** class (`RasterStack`) for all raster stack operations
5. **One** code path in `_collect_images_from_data()`

## Complexity Reduction Summary

| Metric | Before | After |
|--------|--------|-------|
| RasterStack types | 2 (alias + class) | 1 |
| Collection patterns | 3 | 1 |
| Helper functions | 3 | 0 |
| isinstance checks | ~20 (dual) | ~10 (single) |
| Dict literal returns | ~30 | 0 |

## Testing Commands

```bash
# Run all tests
uv run pytest tests/ -v --tb=short

# Run specific test file  
uv run pytest tests/test_truly_lazy_raster_stack.py -v

# Quick import test
uv run python -c "from titiler.openeo.processes.implementations.data_model import RasterStack; print('OK')"
```

## General Instructions

- Validate each step with commits before proceeding
- Run full test suite after each phase
- Keep backwards compatibility during transition
- Use `uv` for all Python commands
- Update devlog as steps are completed

```
