"""titiler.openeo.processes Apply."""

from typing import Any, Callable, Dict, Optional

import morecantile
import numpy

from titiler.openeo.models.openapi import SpatialExtent

from .data_model import ImageData, RasterStack

__all__ = ["apply", "apply_dimension", "xyz_to_bbox", "xyz_to_tileinfo"]


class DimensionNotAvailable(Exception):
    """Exception raised when a dimension is not available."""

    def __init__(self, dimension: str):
        self.dimension = dimension
        super().__init__(
            f"A dimension with the specified name '{dimension}' does not exist."
        )


def apply(
    data: RasterStack,
    process: Callable,
    context: Optional[Dict] = None,
) -> RasterStack:
    """Apply process on RasterStack."""
    positional_parameters = {"x": 0}
    named_parameters = {"context": context}

    def _process_img(img: ImageData):
        return ImageData(
            process(
                img.array,
                positional_parameters=positional_parameters,
                named_parameters=named_parameters,
            ),
            assets=img.assets,
            crs=img.crs,
            bounds=img.bounds,
        )

    # Apply process to each item in the stack
    return {k: _process_img(img) for k, img in data.items()}


def apply_dimension(
    data: RasterStack,
    process: Callable,
    dimension: str,
    target_dimension: Optional[str] = None,
    context: Optional[Dict[str, Any]] = None,
) -> RasterStack:
    """Apply a process to all values along a dimension of a data cube.

    Args:
        data: A RasterStack data cube
        process: Process to be applied on all values along the given dimension.
                The process must accept an array and return an array.
        dimension: The name of the source dimension to apply the process on
        target_dimension: The name of the target dimension or None to use the source dimension
        context: Additional data to be passed to the process

    Returns:
        A data cube with the newly computed values

    Raises:
        DimensionNotAvailable: If the specified dimension does not exist
        ValueError: If the input data is invalid or the process returns invalid data
    """
    # Normalize dimension name
    dim_lower = dimension.lower()

    # Parameters to pass to the process
    positional_parameters = {"data": 0}
    named_parameters = {"context": context}

    # Handle temporal dimension
    if dim_lower in ["t", "temporal", "time"]:
        # If there's only one item in the stack, there's no temporal dimension to apply on
        if len(data) <= 1:
            return data

        return _apply_temporal_dimension(
            data, process, positional_parameters, named_parameters, target_dimension
        )

    # Handle spectral dimension
    elif dim_lower in ["bands", "spectral"]:
        # Check if we have a single-image stack
        if len(data) == 1:
            # Get the single image and apply on its spectral dimension
            key = next(iter(data))
            return {
                key: _apply_spectral_dimension_single_image(
                    data[key], process, positional_parameters, named_parameters
                )
            }
        else:
            return _apply_spectral_dimension_stack(
                data, process, positional_parameters, named_parameters
            )

    # Unsupported dimension
    else:
        raise DimensionNotAvailable(dimension)


def _apply_temporal_dimension(
    data: RasterStack,
    process: Callable,
    positional_parameters: Dict,
    named_parameters: Dict,
    target_dimension: Optional[str],
) -> RasterStack:
    """Apply a process to the temporal dimension of a RasterStack.

    Args:
        data: A RasterStack with temporal dimension
        process: A process function to apply on the temporal dimension
        positional_parameters: Positional parameters for the process
        named_parameters: Named parameters for the process
        target_dimension: Optional target dimension name

    Returns:
        A RasterStack with the process applied to the temporal dimension

    Raises:
        ValueError: If the process returns invalid data
    """
    if not isinstance(data, dict) or not data:
        raise ValueError(
            "Expected a non-empty RasterStack for temporal dimension processing"
        )

    # Apply the process to the temporal dimension
    # The process receives the entire stack and should return modified data
    result_array = process(
        data,  # Pass as positional argument
        positional_parameters=positional_parameters,
        named_parameters=named_parameters,
    )

    # Validate the result
    if not isinstance(result_array, numpy.ndarray):
        raise ValueError(
            "The process must return a numpy array for temporal dimension processing"
        )

    # Get properties from first image
    first_img = next(iter(data.values()))

    # If target_dimension is None, preserve the temporal dimension with processed values
    # Create a new stack with the same keys but processed data
    if target_dimension is None:
        # The result should have shape (n_times, bands, height, width)
        if result_array.shape[0] != len(data):
            raise ValueError(
                f"The process must return an array with the same temporal dimension size. "
                f"Expected {len(data)}, got {result_array.shape[0]}"
            )

        result = {}
        for i, key in enumerate(data.keys()):
            result[key] = ImageData(
                result_array[i],
                assets=[key],
                crs=first_img.crs,
                bounds=first_img.bounds,
                band_names=first_img.band_names if first_img.band_names else [],
                metadata={
                    "applied_dimension": "temporal",
                },
            )
        return result
    else:
        # Replace temporal dimension with target dimension
        # This collapses to a single result
        return {
            target_dimension: ImageData(
                result_array[0] if result_array.shape[0] == 1 else result_array,
                assets=list(data.keys()),
                crs=first_img.crs,
                bounds=first_img.bounds,
                band_names=first_img.band_names if first_img.band_names else [],
                metadata={
                    "applied_dimension": "temporal",
                    "target_dimension": target_dimension,
                },
            )
        }


def _apply_spectral_dimension_single_image(
    data: ImageData,
    process: Callable,
    positional_parameters: Dict,
    named_parameters: Dict,
) -> ImageData:
    """Apply a process to the spectral dimension of a single ImageData.

    Args:
        data: An ImageData with spectral dimension
        process: A process function to apply on the spectral dimension
        positional_parameters: Positional parameters for the process
        named_parameters: Named parameters for the process

    Returns:
        An ImageData with the process applied to the spectral dimension
    """
    # Apply process to the spectral dimension
    # Pass the array (bands, height, width) to the process
    result_array = process(
        data.array,  # Pass the numpy array, not the ImageData object
        positional_parameters=positional_parameters,
        named_parameters=named_parameters,
    )

    # Ensure result maintains spatial dimensions
    # If result is scalar or 1D, broadcast it to spatial dimensions
    if isinstance(result_array, (int, float, numpy.number)):
        # Scalar result - broadcast to spatial shape
        result_array = numpy.full((data.height, data.width), result_array)
    elif result_array.ndim == 1:
        # 1D array - needs to be reshaped
        if len(result_array) == 1:
            # Single value - broadcast to spatial shape
            result_array = numpy.full((data.height, data.width), result_array[0])
        else:
            # Multiple values - this shouldn't happen for spectral reduction to scalar
            result_array = result_array.reshape(
                (len(result_array), data.height, data.width)
            )
    elif result_array.ndim == 2:
        # 2D array (height, width) - add band dimension
        result_array = result_array[numpy.newaxis, :]
    # else: 3D array (bands, height, width) - already correct shape

    return ImageData(
        result_array,
        assets=data.assets,
        crs=data.crs,
        bounds=data.bounds,
        band_names=data.band_names if data.band_names else [],
        metadata={
            "applied_dimension": "spectral",
        },
    )


def _apply_spectral_dimension_stack(
    data: RasterStack,
    process: Callable,
    positional_parameters: Dict,
    named_parameters: Dict,
) -> RasterStack:
    """Apply a process to the spectral dimension of each image in a RasterStack.

    Args:
        data: A RasterStack with spectral dimension
        process: A process function to apply on the spectral dimension
        positional_parameters: Positional parameters for the process
        named_parameters: Named parameters for the process

    Returns:
        A RasterStack with the process applied to the spectral dimension of each image

    Raises:
        ValueError: If the process returns invalid data
    """
    if not isinstance(data, dict) or not data:
        raise ValueError(
            "Expected a non-empty RasterStack for spectral dimension processing"
        )

    # Apply the process to each image in the stack
    result = {}
    for key, img in data.items():
        # Pass the array (bands, height, width) to the process
        result_array = process(
            img.array,  # Pass the numpy array, not the ImageData object
            positional_parameters=positional_parameters,
            named_parameters=named_parameters,
        )

        # Validate the result
        if not isinstance(result_array, numpy.ndarray):
            raise ValueError(
                "The process must return a numpy array for spectral dimension processing"
            )

        # Ensure result maintains spatial dimensions
        # If result is scalar or 1D, broadcast it to spatial dimensions
        if isinstance(result_array, (int, float, numpy.number)):
            # Scalar result - broadcast to spatial shape
            result_array = numpy.full((img.height, img.width), result_array)
        elif result_array.ndim == 1:
            # 1D array - needs to be reshaped
            if len(result_array) == 1:
                # Single value - broadcast to spatial shape
                result_array = numpy.full((img.height, img.width), result_array[0])
            else:
                # Multiple values
                result_array = result_array.reshape(
                    (len(result_array), img.height, img.width)
                )
        elif result_array.ndim == 2:
            # 2D array (height, width) - add band dimension
            result_array = result_array[numpy.newaxis, :]
        # else: 3D array (bands, height, width) - already correct shape

        result[key] = ImageData(
            result_array,
            assets=img.assets,
            crs=img.crs,
            bounds=img.bounds,
            band_names=img.band_names if img.band_names else [],
            metadata={
                "applied_dimension": "spectral",
            },
        )

    return result


def xyz_to_bbox(
    data: Dict[str, Any],
    context: Optional[Dict] = None,
) -> SpatialExtent:
    """Apply process on ArrayLike."""

    # find x, y and z attributes
    if not all(k in data for k in ["x", "y", "z"]):
        raise ValueError("Missing x, y or z attributes in data")
    tile: morecantile.Tile = morecantile.Tile(
        x=data["x"],
        y=data["y"],
        z=data["z"],
    )
    tilematrixset = "WebMercatorQuad"
    tms = morecantile.tms.get(tilematrixset)
    tile_bounds = list(tms.xy_bounds(morecantile.Tile(x=tile.x, y=tile.y, z=tile.z)))
    bbox = SpatialExtent(
        west=tile_bounds[0],
        south=tile_bounds[1],
        east=tile_bounds[2],
        north=tile_bounds[3],
        crs=tms.crs.to_epsg() or tms.crs.to_wkt(),
    )

    return bbox


def xyz_to_tileinfo(
    x: int,
    y: int,
    z: int,
    stage: str = "test",
    context: Optional[Dict] = None,
) -> Dict:
    """Convert XYZ coordinates to tile information."""

    return {
        "x": x,
        "y": y,
        "z": z,
        "stage": stage,
        "context": context or {},
    }
