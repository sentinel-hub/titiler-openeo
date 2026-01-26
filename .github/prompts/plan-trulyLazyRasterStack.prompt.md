# Plan: Make LazyRasterStack Truly Lazy

Refactor `LazyRasterStack` to defer task execution until pixel data is actually needed, by pre-computing cutline masks from STAC item geometry metadata. All output dimensions and metadata will be passed explicitly at construction time. `LazyRasterStack` would wouldreplace `RasterStack` and renamed to `RasterStac` so that all raster stacks are lazy by default.

## Steps

1. **Create `ImageRef` protocol/dataclass** in [data_model.py](titiler/openeo/processes/implementations/data_model.py) with properties: `key`, `geometry`, `width`, `height`, `bounds`, `crs`, `band_names`, `count`, and methods `cutline_mask() -> numpy.ndarray` (computes from geometry) and `realize() -> ImageData` (executes task). This becomes the common interface.

2. **Update `LazyRasterStack.__init__`** in [data_model.py](titiler/openeo/processes/implementations/data_model.py#L111-L145) to accept explicit `width`, `height`, `dst_crs`, `bounds` parameters and store them. Extend `_compute_metadata` to extract geometry from `asset["geometry"]` for each item and create `LazyImageRef` instances.

3. **Add `compute_cutline_mask` utility function** to [data_model.py](titiler/openeo/processes/implementations/data_model.py) that mirrors `_apply_cutline_mask` logic from [reader.py](titiler/openeo/reader.py#L668-L710) — rasterizes geometry to mask using pre-computed transform from bounds/width/height. current _apply_cutline_mask must be removed from reader.

4. **Refactor `_collect_images_from_data`** in [reduce.py](titiler/openeo/processes/implementations/reduce.py#L175-L202) to return `List[Tuple[str, Union[LazyImageRef, ImageData]]]` — for `LazyRasterStack` return refs without executing tasks; for regular `RasterStack` return actual `ImageData`.

5. **Refactor `apply_pixel_selection`** in [reduce.py](titiler/openeo/processes/implementations/reduce.py#L225-L295) to: (a) compute aggregated cutline using `ref.cutline_mask()` from refs without task execution, (b) only call `ref.realize()` inside `_feed_image_to_pixsel` when actually feeding pixels.

6. **Remove `RasterStack` type definition** in [data_model.py](titiler/openeo/processes/implementations/data_model.py#L27). Rename `LazyRasterStack` `RasterStack` and replace all usages.

## Further Considerations

1. **Affine transform calculation**: Use `rasterio.transform.from_bounds(west, south, east, north, width, height)` to compute transform from metadata — this matches what rio-tiler does internally. Confirm this matches actual image transform.

yes

2. **Band count metadata**: STAC items may have band count in `eo:bands` or asset metadata. If not available, we may need to defer `count` until first task execution or require it as explicit parameter. Recommend requiring explicit `bands` list at construction.

yes
