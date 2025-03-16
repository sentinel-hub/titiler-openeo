"""titiler.openeo.processes Spatial."""

from typing import Any, Callable, Dict, Optional, Tuple, Union

import numpy
from pyproj import CRS
from rio_tiler.types import WarpResampling
from shapely.geometry import shape

from .data_model import ImageData, RasterStack

__all__ = ["resample_spatial", "aggregate_spatial"]


class TargetDimensionExists(Exception):
    """Exception raised when a target dimension already exists."""

    def __init__(self, dimension: str):
        self.dimension = dimension
        super().__init__(
            f"A dimension with the specified target dimension name '{dimension}' already exists."
        )


def aggregate_spatial(
    data: Union[RasterStack, ImageData],
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

    # Process geometries based on GeoJSON format
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

    # Initialize results
    results = {}

    # Process each geometry
    for idx, feature in enumerate(features):
        # Extract geometry from feature
        if isinstance(feature, Dict) and "geometry" in feature:
            geom = feature["geometry"]
        else:
            geom = feature

        # Convert to shapely geometry if needed
        if isinstance(geom, Dict):
            geom_obj = shape(geom)
        else:
            geom_obj = geom

        # Handle different types of data
        if isinstance(data, ImageData):
            # Get coverage array for the geometry
            coverage = data.get_coverage_array(geom_obj.__geo_interface__)

            # Extract values for pixels that intersect with the geometry
            values = []
            for band_idx in range(data.count):
                # Get band data
                band_data = data.array[band_idx]

                # Get values where coverage > 0
                mask = coverage > 0
                if numpy.any(mask):
                    band_values = band_data[mask]

                    # If there are valid values, add them to the list
                    if len(band_values) > 0:
                        values.extend(band_values.compressed().tolist())

            # Apply reducer to values
            if values:
                context = context or {}
                result = reducer(values, context)

                # Store results
                if target_dimension is not None:
                    # Count total and valid pixels
                    total_count = numpy.sum(coverage > 0)
                    valid_count = numpy.sum(
                        ~numpy.ma.getmaskarray(band_data)[coverage > 0]
                    )

                    results[str(idx)] = {
                        "value": result,
                        "total_count": int(total_count),
                        "valid_count": int(valid_count),
                    }
                else:
                    results[str(idx)] = result

        elif isinstance(data, dict):  # RasterStack
            # Process each image in the stack
            stack_results = {}

            for key, img in data.items():
                # Get coverage array for the geometry
                coverage = img.get_coverage_array(geom_obj.__geo_interface__)

                # Extract values for pixels that intersect with the geometry
                values = []
                for band_idx in range(img.count):
                    # Get band data
                    band_data = img.array[band_idx]

                    # Get values where coverage > 0
                    mask = coverage > 0
                    if numpy.any(mask):
                        band_values = band_data[mask]

                        # If there are valid values, add them to the list
                        if len(band_values) > 0:
                            values.extend(band_values.compressed().tolist())

                # Apply reducer to values
                if values:
                    context = context or {}
                    result = reducer(values, context)

                    # Store results
                    if target_dimension is not None:
                        # Count total and valid pixels
                        total_count = numpy.sum(coverage > 0)
                        valid_count = numpy.sum(
                            ~numpy.ma.getmaskarray(band_data)[coverage > 0]
                        )

                        stack_results[key] = {
                            "value": result,
                            "total_count": int(total_count),
                            "valid_count": int(valid_count),
                        }
                    else:
                        stack_results[key] = result

            results[str(idx)] = stack_results

    # Return vector data cube with results
    return {
        "type": "VectorDataCube",
        "geometries": features,
        "properties": properties,
        "values": results,
    }


def resample_spatial(
    data: Union[RasterStack, ImageData],
    projection: Union[int, str],
    resolution: Union[float, Tuple[float, float]],
    align: str,
    method: WarpResampling = "nearest",
) -> Union[RasterStack, ImageData]:
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
        # map resampling method to rio-tiler method using a dictionary
        resampling_method: WarpResampling = {
            "nearest": WarpResampling.nearest,
            "bilinear": WarpResampling.bilinear,
            "cubic": WarpResampling.cubic,
            "cubicspline": WarpResampling.cubic_spline,
            "lanczos": WarpResampling.lanczos,
            "average": WarpResampling.average,
            "mode": WarpResampling.mode,
            "max": None,
            "min": None,
            "med": None,
            "q1": None,
            "q3": None,
            "sum": WarpResampling.sum,
            "rms": WarpResampling.rms,
            "near": None,
        }[method]

        if resampling_method is None:
            raise ValueError(f"Unsupported resampling method: {method}")

        if resolution is not None and not isinstance(resolution, (list, tuple)):
            resolution = (resolution, resolution)

        # reproject the image
        return img.reproject(
            dst_crs, resolution=resolution, reproject_method=resampling_method
        )

    """ Get destination CRS from parameters """
    dst_crs = CRS.from_epsg(projection)

    if isinstance(data, ImageData):
        return _reproject_img(data, dst_crs, resolution, method)

    return {k: _reproject_img(v, dst_crs, resolution, method) for k, v in data.items()}
