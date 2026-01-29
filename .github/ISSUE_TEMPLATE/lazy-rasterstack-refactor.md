# Make RasterStack Truly Lazy

## Problem

`LazyRasterStack` is not actually lazy — task execution is triggered too early when collecting metadata (specifically cutline masks). This defeats the purpose of lazy loading and can cause performance issues when only a subset of data is needed.

## Objective

Refactor `LazyRasterStack` so that:

1. **Cutline masks** are computed from STAC item geometry metadata, not from executed tasks
2. **Task execution** is deferred until pixel data is actually needed (e.g., when feeding to pixel selection)
3. **Output dimensions** (width, height, bounds, CRS, bands) are passed explicitly at construction time

## Proposed Changes

### 1. Introduce `ImageRef` abstraction

Create an `ImageRef` protocol/dataclass that represents a reference to an image without loading it:

- Stores metadata: `key`, `geometry`, `width`, `height`, `bounds`, `crs`, `band_names`, `count`
- `cutline_mask()` — computes mask from geometry without task execution
- `realize()` — executes task and returns `ImageData`

### 2. Unify RasterStack types

- Rename `LazyRasterStack` → `RasterStack` (all stacks become lazy by default)
- Remove the old `RasterStack` type alias
- This is a **breaking change**

### 3. Compute cutline masks lazily

Add a `compute_cutline_mask()` utility that rasterizes geometry using `rasterio.transform.from_bounds()`. Remove `_apply_cutline_mask` from `reader.py` (consolidate logic).

### 4. Update reduction pipeline

Refactor `apply_pixel_selection` in `reduce.py` to:

- Collect `ImageRef` instances first (no task execution)
- Compute aggregated cutline mask from refs
- Only call `ref.realize()` when actually feeding pixels

### 5. Cleanup

Remove deprecated `load_collection_and_reduce` function from `stacapi.py`.

## Technical Notes

- **Affine transform**: Use `rasterio.transform.from_bounds(west, south, east, north, width, height)`
- **Band count**: Require explicit `bands` list at construction; derive count from list length
- **No backward compatibility** — update all existing usages

## Acceptance Criteria

- [ ] Cutline masks computed without executing reader tasks
- [ ] Tasks only execute when `ImageData` is actually needed
- [ ] All existing tests pass with updated behavior
- [ ] New tests for `ImageRef` interface and lazy execution
