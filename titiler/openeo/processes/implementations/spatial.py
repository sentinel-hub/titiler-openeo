"""titiler.openeo.processes Spatial."""

from typing import Any, Callable, Dict, List, Optional, Tuple, Union

import numpy
from pyproj import CRS
from rasterio.crs import CRS as RioCRS
from rasterio.enums import Resampling
from rasterio.transform import array_bounds
from rasterio.transform import from_bounds as transform_from_bounds
from rasterio.warp import calculate_default_transform
from rasterio.warp import reproject as rio_reproject
from rio_tiler.utils import resize_array

from .data_model import ImageData, RasterStack, compute_cutline_mask

__all__ = [
    "resample_spatial",
    "resample_cube_spatial",
    "aggregate_spatial",
    "mask_polygon",
    "mask",
]

# openEO resampling method names -> rasterio.enums.Resampling members.
_RESAMPLING_METHODS = {
    "near": "nearest",
    "nearest": "nearest",
    "bilinear": "bilinear",
    "cubic": "cubic",
    "cubicspline": "cubic_spline",
    "lanczos": "lanczos",
    "average": "average",
    "mode": "mode",
    "max": "max",
    "min": "min",
    "med": "med",
    "q1": "q1",
    "q3": "q3",
    "rms": "rms",
    "sum": "sum",
}


def _resolve_resampling(method: str) -> Resampling:
    """Map an openEO resampling method name to a rasterio Resampling member."""
    if method not in _RESAMPLING_METHODS:
        raise ValueError(f"Unsupported resampling method: {method}")
    return Resampling[_RESAMPLING_METHODS[method]]


def _warp_image_to_grid(
    img: ImageData,
    dst_crs: Any,
    dst_transform: Any,
    dst_width: int,
    dst_height: int,
    resampling: Resampling,
) -> ImageData:
    """Warp one image onto an explicit destination grid (crs + transform + size).

    Shared by ``resample_spatial`` (grid from CRS+resolution) and
    ``resample_cube_spatial`` (grid from a target cube). The data is warped with the
    requested method and the nodata mask is warped with nearest neighbour so
    valid/nodata regions follow the data and uncovered pixels stay masked.
    """
    src = img.array
    bands = src.shape[0]
    dtype = src.dtype
    src_crs = RioCRS.from_user_input(img.crs)
    out_crs = RioCRS.from_user_input(dst_crs)
    src_transform = transform_from_bounds(*img.bounds, img.width, img.height)

    dst_data = numpy.zeros((bands, dst_height, dst_width), dtype=dtype)
    rio_reproject(
        numpy.ascontiguousarray(src.data),
        dst_data,
        src_transform=src_transform,
        src_crs=src_crs,
        dst_transform=dst_transform,
        dst_crs=out_crs,
        resampling=resampling,
    )

    src_mask = numpy.ma.getmaskarray(src).astype("uint8")
    dst_mask = numpy.ones((bands, dst_height, dst_width), dtype="uint8")
    rio_reproject(
        numpy.ascontiguousarray(src_mask),
        dst_mask,
        src_transform=src_transform,
        src_crs=src_crs,
        dst_transform=dst_transform,
        dst_crs=out_crs,
        resampling=Resampling.nearest,
        src_nodata=1,
        dst_nodata=1,
    )

    out = numpy.ma.masked_array(dst_data, mask=dst_mask.astype(bool))
    west, south, east, north = array_bounds(dst_height, dst_width, dst_transform)
    return ImageData(
        out,
        assets=img.assets,
        crs=out_crs,
        bounds=(west, south, east, north),
        band_descriptions=img.band_descriptions,
        metadata=img.metadata,
    )


class IncompatibleDataCubes(Exception):
    """Exception raised when the data and mask data cubes are incompatible."""

    def __init__(
        self,
        message: str = (
            "The data cube and the mask are incompatible, e.g. because of "
            "different dimensions or labels."
        ),
    ):
        super().__init__(message)


class TargetDimensionExists(Exception):
    """Exception raised when a target dimension already exists."""

    def __init__(self, dimension: str):
        self.dimension = dimension
        super().__init__(
            f"A dimension with the specified target dimension name '{dimension}' already exists."
        )


def _process_geometries(geometries: Union[Dict, Any]) -> Tuple[List[Any], List[Dict]]:
    """Process geometries based on GeoJSON format.

    Args:
        geometries: Geometries for which the aggregation will be computed.

    Returns:
        A tuple containing a list of features and a list of properties.

    Raises:
        ValueError: If the GeoJSON format is invalid.
    """
    features = []
    properties = []

    if isinstance(geometries, Dict):
        if "type" in geometries:
            if geometries["type"] == "FeatureCollection":
                features = geometries["features"]
                properties = [feature.get("properties", {}) for feature in features]
            elif geometries["type"] == "Feature":
                features = [geometries]
                properties = [geometries.get("properties", {})]
            else:  # Assume it's a Geometry
                features = [
                    {"type": "Feature", "geometry": geometries, "properties": {}}
                ]
                properties = [{}]
        else:
            raise ValueError("Invalid GeoJSON format")
    else:
        # Assume it's already a list of features or similar structure
        features = geometries
        properties = [feature.get("properties", {}) for feature in features]

    return features, properties


def _extract_geometry(feature: Any) -> Any:
    """Extract geometry from a feature.

    Args:
        feature: A GeoJSON feature or geometry.

    Returns:
        The geometry object.
    """
    if isinstance(feature, Dict) and "geometry" in feature:
        return feature["geometry"]
    return feature


def _apply_reducer_to_image(
    img: ImageData,
    geom_obj: Any,
    reducer: Callable,
    target_dimension: Optional[str] = None,
) -> Any:
    """Apply reducer to an ImageData object.

    Args:
        img: An ImageData object.
        geom_obj: A GeoJSON geometry dict or object with __geo_interface__ property.
        reducer: A reducer function.
        target_dimension: Optional dimension name to store additional information.

    Returns:
        The result of applying the reducer.
    """
    # Get coverage array for the geometry
    # Handle both dict and objects with __geo_interface__
    geom_dict = geom_obj if isinstance(geom_obj, dict) else geom_obj.__geo_interface__
    coverage = img.get_coverage_array(geom_dict)

    for band_idx in range(img.count):
        # Get band data
        band_data = img.array[band_idx]

        # Get values where coverage > 0
        mask = coverage > 0
        if numpy.any(mask):
            band_values = band_data[mask]

            # If there are no valid values, skip
            if len(band_values) == 0:
                return None

            result = reducer(data=band_values)

            # Store results with additional information if target_dimension is provided
            if target_dimension is not None:
                # Count total and valid pixels
                total_count = numpy.sum(coverage > 0)
                valid_count = numpy.sum(~numpy.ma.getmaskarray(band_data)[coverage > 0])

                return {
                    "value": result,
                    "total_count": int(total_count),
                    "valid_count": int(valid_count),
                }
            return result

    return None


def _apply_reducer_to_raster_stack(
    data: RasterStack,
    geom_obj: Any,
    reducer: Callable,
    target_dimension: Optional[str] = None,
) -> Dict[str, Any]:
    """Apply reducer to a RasterStack.

    Args:
        data: A RasterStack object.
        geom_obj: A GeoJSON geometry dict or object with __geo_interface__ property.
        reducer: A reducer function.
        target_dimension: Optional dimension name to store additional information.

    Returns:
        A dictionary mapping keys to reducer results.
    """
    stack_results = {}

    for key, img in data.items():
        result = _apply_reducer_to_image(img, geom_obj, reducer, target_dimension)
        if result is not None:
            stack_results[key] = result

    return stack_results


def _create_feature_collection(
    features: List[Any],
    properties: List[Dict],
    results: Dict[str, Any],
) -> Dict[str, Any]:
    """Create a GeoJSON FeatureCollection from features, properties, and results.

    Args:
        features: A list of GeoJSON features.
        properties: A list of properties for each feature.
        results: A dictionary mapping feature indices to reducer results.

    Returns:
        A GeoJSON FeatureCollection.
    """
    # Initialize with explicit typing for features list
    features_list: List[Dict[str, Any]] = []
    feature_collection: Dict[str, Any] = {
        "type": "FeatureCollection",
        "features": features_list,
    }

    for idx, feature in enumerate(features):
        idx_str = str(idx)
        if idx_str in results:
            # Extract geometry from feature
            geometry = _extract_geometry(feature)

            # Create a new feature with geometry, properties, and values
            new_feature = {
                "type": "Feature",
                "geometry": geometry,
                "properties": properties[idx].copy() if idx < len(properties) else {},
            }

            # Add values to properties
            new_feature["properties"]["values"] = results[idx_str]

            # Add to feature collection
            features_list.append(new_feature)

    return feature_collection


def aggregate_spatial(
    data: RasterStack,
    geometries: Union[Dict, Any],
    reducer: Callable,
    target_dimension: Optional[str] = None,
    context: Optional[Any] = None,
) -> Dict[str, Any]:
    """Aggregates statistics for one or more geometries over the spatial dimensions.

    Args:
        data: A raster data cube with at least two spatial dimensions.
        geometries: Geometries for which the aggregation will be computed.
        reducer: A reducer to be applied on all values of each geometry.
        target_dimension: Optional dimension name to store additional information.
        context: Additional data to be passed to the reducer.

    Returns:
        A vector data cube with the computed results.

    Raises:
        TargetDimensionExists: If a dimension with the specified target dimension name already exists.
    """
    # Check if target_dimension exists
    if target_dimension is not None:
        # In a real implementation, we would check if the dimension exists
        # For now, we'll just assume it doesn't
        pass

    # Process geometries
    features, properties = _process_geometries(geometries)

    # Initialize results
    results = {}

    # Process each geometry
    for idx, feature in enumerate(features):
        # Extract geometry (already a GeoJSON dict)
        geom = _extract_geometry(feature)

        # Apply reducer to the RasterStack
        stack_results = _apply_reducer_to_raster_stack(
            data, geom, reducer, target_dimension
        )
        if stack_results:
            results[str(idx)] = stack_results

    # Create and return GeoJSON FeatureCollection
    return _create_feature_collection(features, properties, results)


def resample_spatial(
    data: RasterStack,
    projection: Optional[Union[int, str]] = None,
    resolution: Union[float, Tuple[float, float], None] = 0,
    align: str = "upper-left",
    method: str = "near",
) -> RasterStack:
    """Resample and warp the spatial dimensions of the raster at a given resolution."""

    def _reproject_img(
        img: ImageData,
        dst_crs: Optional[CRS],
        resolution: Union[float, Tuple[float, float], None],
        method: str,
    ) -> ImageData:
        # align parameter is accepted but not yet implemented
        # We silently ignore it for now (uses GDAL defaults)

        # If no projection change requested and no resolution change, return as-is
        if dst_crs is None and (resolution is None or resolution == 0):
            return img

        resampling = _resolve_resampling(method)
        # Use the image's existing CRS if no new projection specified
        target_crs = dst_crs if dst_crs is not None else img.crs

        if (
            resolution is not None
            and resolution != 0
            and not isinstance(resolution, (list, tuple))
        ):
            resolution = (resolution, resolution)
        actual_resolution = None if resolution == 0 else resolution

        # Derive the destination grid for this CRS/resolution (GDAL default extent),
        # then warp onto it via the shared helper used by resample_cube_spatial.
        dst_transform, dst_width, dst_height = calculate_default_transform(
            RioCRS.from_user_input(img.crs),
            RioCRS.from_user_input(target_crs),
            img.width,
            img.height,
            *img.bounds,
            resolution=actual_resolution,
        )
        return _warp_image_to_grid(
            img, target_crs, dst_transform, dst_width, dst_height, resampling
        )

    # Get destination CRS from parameters (None if not specified)
    dst_crs: Optional[CRS] = None
    if projection is not None:
        if isinstance(projection, int):
            dst_crs = CRS.from_epsg(projection)
        else:
            dst_crs = CRS.from_user_input(projection)

    # Reproject each image in the stack
    return RasterStack.from_images(
        {k: _reproject_img(v, dst_crs, resolution, method) for k, v in data.items()}
    )


def _target_spatial_grid(
    target: RasterStack,
) -> Tuple[Optional[CRS], Tuple[float, float, float, float], int, int]:
    """Return (crs, bounds, width, height) of the target cube's spatial grid.

    Reads ImageRef metadata first (no pixel load); falls back to realizing the
    first image when the refs don't carry spatial dimensions.
    """
    refs = target.get_image_refs()
    if refs:
        _, ref = refs[0]
        if ref.crs is not None and ref.bounds and ref.width and ref.height:
            return ref.crs, tuple(ref.bounds), ref.width, ref.height  # type: ignore[return-value]
    img = target.first
    return img.crs, tuple(img.bounds), img.width, img.height  # type: ignore[return-value]


def _resample_image_to_grid(
    img: ImageData,
    dst_crs: Optional[CRS],
    dst_bounds: Tuple[float, float, float, float],
    dst_width: int,
    dst_height: int,
    resampling: Resampling,
) -> ImageData:
    """Resample a single image onto the target (crs, bounds, width, height) grid."""
    # Already aligned: nothing to do.
    if (
        img.crs == dst_crs
        and img.width == dst_width
        and img.height == dst_height
        and img.bounds is not None
        and numpy.allclose(img.bounds, dst_bounds)
    ):
        return img

    # Without georeferencing we can only resize to the target size.
    if img.crs is None or img.bounds is None:
        src = img.array
        resized = resize_array(src.data, dst_height, dst_width)
        resized_mask = resize_array(
            numpy.ma.getmaskarray(src).astype("uint8"), dst_height, dst_width
        ).astype(bool)
        return ImageData(
            numpy.ma.masked_array(resized, mask=resized_mask),
            assets=img.assets,
            crs=dst_crs,
            bounds=dst_bounds,
            band_descriptions=img.band_descriptions,
            metadata=img.metadata,
        )

    # Warp onto the exact target grid via the shared helper.
    dst_transform = transform_from_bounds(*dst_bounds, dst_width, dst_height)
    return _warp_image_to_grid(
        img, dst_crs, dst_transform, dst_width, dst_height, resampling
    )


def resample_cube_spatial(
    data: RasterStack,
    target: RasterStack,
    method: str = "near",
) -> RasterStack:
    """Resample the spatial dimensions of ``data`` to match the ``target`` cube.

    Each image in ``data`` is warped onto the target cube's spatial grid (CRS,
    bounds and width/height). Non-spatial dimensions (time, bands) are preserved.

    Args:
        data: The source raster data cube.
        target: A raster data cube describing the target spatial grid.
        method: Resampling method (gdalwarp names, e.g. ``near`` (default),
            ``bilinear``, ``cubic``, ``average``, ``min``, ``max``, ``med`` ...).

    Returns:
        The source cube resampled onto the target's spatial grid.
    """
    if not data:
        raise ValueError("Expected a non-empty data cube")
    if not target:
        raise ValueError("Expected a non-empty target data cube")

    resampling = _resolve_resampling(method)

    dst_crs, dst_bounds, dst_width, dst_height = _target_spatial_grid(target)

    return RasterStack.from_images(
        {
            key: _resample_image_to_grid(
                img, dst_crs, dst_bounds, dst_width, dst_height, resampling
            )
            for key, img in data.items()
        }
    )


def _extract_geometries_from_mask(
    mask: Dict,
) -> List[Dict[str, Any]]:
    """Extract a list of GeoJSON geometry dicts from a mask parameter.

    Supports Polygon, MultiPolygon, Feature, and FeatureCollection inputs.
    Feature and raw geometries are normalized to FeatureCollection internally.

    Args:
        mask: GeoJSON object (Geometry, Feature, or FeatureCollection).

    Returns:
        List of GeoJSON geometry dicts.
    """
    if not isinstance(mask, dict) or "type" not in mask:
        raise ValueError("Invalid GeoJSON: must be a dict with a 'type' field.")

    geom_type = mask["type"]

    # Normalize to FeatureCollection
    if geom_type in ("Polygon", "MultiPolygon"):
        mask = {
            "type": "FeatureCollection",
            "features": [{"type": "Feature", "geometry": mask, "properties": {}}],
        }
    elif geom_type == "Feature":
        mask = {"type": "FeatureCollection", "features": [mask]}
    elif geom_type != "FeatureCollection":
        raise ValueError(
            f"Unsupported GeoJSON type '{geom_type}'. "
            "Expected Polygon, MultiPolygon, Feature, or FeatureCollection."
        )

    geometries = []
    for feature in mask.get("features", []):
        geom = feature.get("geometry")
        if not geom:
            continue
        geom_geom_type = geom.get("type")
        if geom_geom_type not in ("Polygon", "MultiPolygon"):
            raise ValueError(
                f"Unsupported geometry type '{geom_geom_type}'. "
                "Only Polygon and MultiPolygon geometries are allowed in mask."
            )
        geometries.append(geom)

    return geometries


def mask_polygon(
    data: RasterStack,
    mask: Dict,
    replacement: Optional[Union[int, float, bool, str]] = None,
    inside: bool = False,
) -> RasterStack:
    """Apply a polygon mask to a raster data cube.

    All pixels for which the point at the pixel center does not intersect
    with any polygon are replaced. This behavior can be inverted by setting
    ``inside`` to ``True``.

    Args:
        data: A raster data cube.
        mask: A GeoJSON object containing polygon geometries.
        replacement: Value to replace masked pixels with. None uses no-data.
        inside: If True, pixels inside the polygon are replaced instead.

    Returns:
        A masked raster data cube with the same dimensions.
    """
    geometries = _extract_geometries_from_mask(mask)

    if not geometries:
        return data

    # Compute the rasterized geometry mask once using the first image's grid.
    # RasterStack shares spatial extent/CRS across all images.
    first_img = next(iter(data.values()))
    # compute_cutline_mask returns True for pixels OUTSIDE geometry,
    # so we invert to get True for pixels that INTERSECT.
    intersects = ~compute_cutline_mask(
        geometries,
        width=first_img.width,
        height=first_img.height,
        bounds=first_img.bounds,
        dst_crs=first_img.crs,
    )

    # Determine which pixels to replace:
    # Default (inside=False): replace pixels OUTSIDE the polygon (not intersecting)
    # inside=True: replace pixels INSIDE the polygon (intersecting)
    if inside:
        pixels_to_replace = intersects  # 2D (height, width)
    else:
        pixels_to_replace = ~intersects  # 2D (height, width)

    result_images = {}

    for key, img in data.items():
        # Work on a copy of the data
        new_data = img.array.copy()
        new_mask = numpy.ma.getmaskarray(img.array).copy()

        if replacement is None:
            # Set to no-data by updating the mask
            for band_idx in range(new_data.shape[0]):
                # Preserve existing no-data: only apply to currently valid pixels
                valid = ~new_mask[band_idx]
                new_mask[band_idx] |= pixels_to_replace & valid
        else:
            # Replace with the specified value
            for band_idx in range(new_data.shape[0]):
                valid = ~new_mask[band_idx]
                apply_here = pixels_to_replace & valid
                new_data[band_idx][apply_here] = replacement

        masked_array = numpy.ma.MaskedArray(new_data, mask=new_mask)

        result_images[key] = ImageData(
            masked_array,
            assets=img.assets,
            crs=img.crs,
            bounds=img.bounds,
            band_names=img.band_names,
            band_descriptions=img.band_descriptions,
        )

    return RasterStack.from_images(result_images)


def _mask_active_array(
    mask_img: ImageData,
    data_bands: int,
    height: int,
    width: int,
) -> numpy.ndarray:
    """Compute a boolean (bands, height, width) array of pixels to replace.

    A mask pixel is "active" (True, i.e. its counterpart in ``data`` must be
    replaced) where the mask value is non-zero (for numbers) or ``True`` (for
    booleans) and the mask pixel itself is valid (not no-data).

    The mask is aligned to the ``data`` grid:
    - Spatially resized (nearest-neighbour) when the mask resolution differs.
    - Broadcast across bands when the mask has a single band; otherwise the
      mask must have exactly the same number of bands as ``data``.

    Raises:
        IncompatibleDataCubes: If the mask band count is neither 1 nor equal to
            the data band count.
    """
    marr = mask_img.array
    # Active where the mask value is non-zero/true and the pixel is valid.
    active = (marr.data != 0) & (~numpy.ma.getmaskarray(marr))

    # Resize spatially (nearest-neighbour) when grids differ.
    if active.shape[-2:] != (height, width):
        active = resize_array(active.astype("uint8"), height, width).astype(bool)

    mask_bands = active.shape[0]
    if mask_bands == data_bands:
        return active
    if mask_bands == 1:
        return numpy.repeat(active, data_bands, axis=0)

    raise IncompatibleDataCubes(
        f"The mask has {mask_bands} bands which is incompatible with the "
        f"{data_bands} bands of the data cube. The mask must have either a "
        f"single band or the same number of bands as the data."
    )


def mask(
    data: RasterStack,
    mask: RasterStack,
    replacement: Optional[Union[int, float, bool, str]] = None,
) -> RasterStack:
    """Apply a raster mask to a raster data cube.

    Pixels in ``data`` are replaced where the corresponding pixel in ``mask`` is
    non-zero (for numbers) or ``True`` (for booleans). The replacement value
    defaults to the no-data value of ``data`` (i.e. the pixel is masked out).

    The mask is aligned to ``data``: spatial dimensions are resampled implicitly
    when resolutions differ, and a mask with a single temporal label (or a
    single band) is broadcast across the matching dimension of ``data``. When
    the mask has a temporal dimension, its labels must match those of ``data``.

    Args:
        data: A raster data cube.
        mask: A raster data cube used as the mask.
        replacement: The value used to replace masked pixels. ``None`` replaces
            them with the no-data value of ``data``.

    Returns:
        A masked raster data cube with the same dimensions as ``data``.

    Raises:
        IncompatibleDataCubes: If ``data`` and ``mask`` cannot be aligned, e.g.
            because of mismatched temporal labels or band counts.
    """
    if not data:
        return data
    if not mask:
        raise IncompatibleDataCubes("The mask data cube is empty.")

    mask_keys = list(mask.keys())
    # A mask with a single temporal label is broadcast across all data labels.
    broadcast = len(mask_keys) == 1

    result_images = {}
    for key, img in data.items():
        if broadcast:
            mask_img = mask[mask_keys[0]]
        elif key in mask:
            mask_img = mask[key]
        else:
            raise IncompatibleDataCubes(
                f"The mask has no temporal label matching '{key}' in the data cube."
            )

        if mask_img.crs != img.crs or mask_img.bounds != img.bounds:
            raise IncompatibleDataCubes(
                "The mask and data cube must have the same CRS and bounds (spatial extent) for masking."
            )

        new_data = img.array.data.copy()
        new_mask = numpy.ma.getmaskarray(img.array).copy()
        active = _mask_active_array(mask_img, new_data.shape[0], img.height, img.width)
        # Only replace pixels that are currently valid, preserving existing
        # no-data, consistent with mask_polygon.
        apply_here = active & (~new_mask)

        if replacement is None:
            new_mask |= apply_here
        else:
            new_data[apply_here] = replacement

        masked_array = numpy.ma.MaskedArray(new_data, mask=new_mask)
        result_images[key] = ImageData(
            masked_array,
            assets=img.assets,
            crs=img.crs,
            bounds=img.bounds,
            band_names=img.band_names,
            band_descriptions=img.band_descriptions,
        )

    return RasterStack.from_images(result_images)
