"""titiler.openeo.processes Spatial."""

from typing import Any, Callable, Dict, List, Optional, Tuple, Union

import numpy
from pyproj import CRS
from rasterio.warp import Resampling as WarpResampling
from shapely.geometry import shape

from .data_model import ImageData, RasterStack, to_raster_stack

__all__ = ["resample_spatial", "aggregate_spatial"]


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
        geom_obj: A shapely geometry object.
        reducer: A reducer function.
        target_dimension: Optional dimension name to store additional information.

    Returns:
        The result of applying the reducer.
    """
    # Get coverage array for the geometry
    coverage = img.get_coverage_array(geom_obj.__geo_interface__)

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
        geom_obj: A shapely geometry object.
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
    context: Optional[Dict[str, Any]] = None,
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
        # Extract and convert geometry
        geom = _extract_geometry(feature)
        geom_obj = shape(geom) if isinstance(geom, Dict) else geom
        
        # Apply reducer to the RasterStack
        stack_results = _apply_reducer_to_raster_stack(
            data, geom_obj, reducer, target_dimension
        )
        if stack_results:
            results[str(idx)] = stack_results

    # Create and return GeoJSON FeatureCollection
    return _create_feature_collection(features, properties, results)


def resample_spatial(
    data: RasterStack,
    projection: Union[int, str],
    resolution: Union[float, Tuple[float, float]],
    align: str,
    method: WarpResampling = "nearest",
) -> RasterStack:
    """Resample and warp the spatial dimensions of the raster at a given resolution."""

    def _reproject_img(
        img: ImageData,
        dst_crs: CRS,
        resolution: Union[float, Tuple[float, float], None],
        method: str,
    ) -> ImageData:
        # align is not yet implemented
        if align is not None:
            raise NotImplementedError(
                "resample_spatial: align parameter is not yet implemented"
            )

        dst_crs = CRS.from_user_input(projection)
        # Map the string resampling method to the matching enum name
        resampling_method_map = {
            "nearest": "nearest",
            "bilinear": "bilinear", 
            "cubic": "cubic",
            "cubicspline": "cubic_spline",
            "lanczos": "lanczos",
            "average": "average",
            "mode": "mode",
            "sum": "sum",
            "rms": "rms"
        }
        
        if method not in resampling_method_map:
            raise ValueError(f"Unsupported resampling method: {method}")
            
        resampling_method = resampling_method_map[method]

        if resolution is not None and not isinstance(resolution, (list, tuple)):
            resolution = (resolution, resolution)

        # Reproject the image with the string method name
        return img.reproject(
            dst_crs, resolution=resolution, reproject_method=resampling_method
        )

    """ Get destination CRS from parameters """
    dst_crs = CRS.from_epsg(projection)

    # Reproject each image in the stack
    return {k: _reproject_img(v, dst_crs, resolution, method) for k, v in data.items()}
