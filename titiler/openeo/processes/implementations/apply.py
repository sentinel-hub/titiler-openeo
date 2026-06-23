"""titiler.openeo.processes Apply.

CRITICAL DEVELOPER WARNING - CALLBACK INVOCATION PATTERN
========================================================

The ``process``/callback passed to ``apply``, ``apply_dimension`` and ``array_apply``
is an openEO process graph compiled by openeo_pg_parser_networkx. Its ``node_callable``
memoizes each node's result in a **shared** ``results_cache`` (and mutates the graph's
``resolved_kwargs`` on every call). Therefore you MUST call each callback EXACTLY ONCE
per operation.

KEY REQUIREMENTS:
-----------------
1. **NEVER iterate and call the callback per element/image**
   ❌ BAD:  results = [process(x=item) for item in data]
   ✅ GOOD: stacked = stack_all(data); result = process(x=stacked)

   Calling the callback more than once returns the FIRST call's cached result for
   every subsequent call (and, run concurrently, corrupts argument resolution —
   e.g. a child ``max`` ends up with no ``data``). Rely on numpy broadcasting to map
   a vectorized callback over every element in a single call instead.

2. **Pass the callback a REALIZED numpy array, not a lazy/duck-array view.**
   Many processes type-check their input with ``isinstance(x, numpy.ndarray)``
   (e.g. ``max``, ``array_element``), so a lazy view is rejected. Realize the stack
   before calling the callback.

3. **Forward the enclosing ``named_parameters``.** A callback may reference an
   outer-scope parameter via ``from_parameter`` — e.g. ``array_apply`` running
   ``neq(x, max(data))`` where ``data`` is the enclosing ``apply_dimension`` array.
   When a process invokes a child callback it must merge its own parameters on top
   of the inherited ones, never replace them.

4. **Why this matters:** the single-call rule is the same caching pitfall documented
   in reduce.py; it has caused production issues (silent wrong results) more than once.

5. **Testing requirements:** cover these processes with *process-graph* integration
   tests (build an ``OpenEOProcessGraph`` and run it via ``to_callable``), not only
   plain-Python callbacks — the latter have no shared cache and hide the bug.

``_apply_spectral_dimension_stack`` evaluates the callback ONCE on the whole stack
(bands moved to the front, broadcasting across time) for exactly this reason.
"""

from typing import Any, Callable, Dict, Optional

import morecantile
import numpy
from openeo_pg_parser_networkx.pg_schema import BoundingBox

from .data_model import ImageData, RasterStack

__all__ = ["apply", "apply_dimension", "xyz_to_bbox", "xyz_to_tileinfo"]


def _stack_rasterstack(data: RasterStack) -> numpy.ma.MaskedArray:
    """Realize a RasterStack into a ``(n, bands, height, width)`` masked array.

    Callbacks must receive a **real** numpy array, not a lazy view: many openEO
    processes type-check their input with ``isinstance(x, numpy.ndarray)``
    (e.g. ``max``, ``array_element``), which any lazy/duck-array fails. Stacking
    here realizes each image exactly once (cached on the RasterStack), which is
    unavoidable for a temporal/whole-cube operation anyway.
    """
    return numpy.ma.stack([data[key].array for key in data.keys()])


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
    context: Optional[Any] = None,
) -> RasterStack:
    """Apply a unary process to every value in a RasterStack.

    The callback is evaluated ONCE on the stacked array (see the module-level
    warning): images are stacked to ``(n, bands, height, width)``, the vectorized
    callback maps over every value, then the result is split back per timestamp.
    Calling the callback per image (the previous design) returned the first image's
    cached result for every subsequent image.
    """
    keys = list(data.keys())
    if not keys:
        return data

    # Stack to a realized (n, bands, h, w) array and call the callback ONCE.
    stacked = _stack_rasterstack(data)
    result = numpy.asanyarray(
        process(
            stacked,
            positional_parameters={"x": 0},
            named_parameters={"x": stacked, "context": context},
        )
    )

    # Preserve each image's metadata. Realizing the stack above already cached the
    # ImageData instances, so ``data[key]`` is a cache hit (no extra I/O).
    out: Dict[Any, ImageData] = {}
    for i, key in enumerate(keys):
        img = data[key]
        out[key] = ImageData(
            result[i],
            assets=img.assets,
            crs=img.crs,
            bounds=img.bounds,
            band_descriptions=img.band_descriptions,
        )
    return RasterStack.from_images(out)


def apply_dimension(
    data: RasterStack,
    process: Callable,
    dimension: str,
    target_dimension: Optional[str] = None,
    context: Optional[Any] = None,
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
            result_img = _apply_spectral_dimension_single_image(
                data[key], process, positional_parameters, named_parameters
            )
            return RasterStack.from_images({key: result_img})
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

    The callback receives the temporal series as a lazy array view of shape
    ``(n_times, bands, height, width)`` (realized on access via ``numpy.asarray``)
    and must return a numpy array.

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

    # Per the openEO ``apply_dimension`` spec the callback receives the values
    # along the dimension as an array (a labeled array), not the datacube itself.
    # It must be a realized numpy array: callbacks include processes that type-check
    # with ``isinstance(x, numpy.ndarray)`` (e.g. ``max``).
    stacked = _stack_rasterstack(data)

    # Apply the process to the temporal dimension (call the callback ONCE)
    result_array = process(
        stacked,  # Pass as positional argument: (n_times, bands, height, width)
        positional_parameters=positional_parameters,
        named_parameters=named_parameters,
    )

    # Validate the result
    if not isinstance(result_array, numpy.ndarray):
        raise ValueError(
            "The process must return a numpy array for temporal dimension processing"
        )

    # Get metadata from first ImageRef WITHOUT loading pixel data
    image_refs = data.get_image_refs()
    if not image_refs:
        raise ValueError("No image refs available for metadata")
    first_key, first_ref = image_refs[0]

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
                crs=first_ref.crs,
                bounds=first_ref.bounds,
                band_descriptions=first_ref.band_names if first_ref.band_names else [],
                metadata={
                    "applied_dimension": "temporal",
                },
            )
        return RasterStack.from_images(result)
    else:
        # Replace temporal dimension with target dimension
        # This collapses to a single result
        result_img = ImageData(
            result_array[0] if result_array.shape[0] == 1 else result_array,
            assets=list(data.keys()),
            crs=first_ref.crs,
            bounds=first_ref.bounds,
            band_descriptions=first_ref.band_names if first_ref.band_names else [],
            metadata={
                "applied_dimension": "temporal",
                "target_dimension": target_dimension,
            },
        )
        # Use the first key from the image_refs as the result key
        return RasterStack.from_images({first_key: result_img})


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
        band_descriptions=data.band_descriptions if data.band_descriptions else [],
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

    # CRITICAL: evaluate the callback EXACTLY ONCE on the whole stack, never once
    # per image. The openEO executor (openeo_pg_parser_networkx) memoizes each
    # callback node by id in a shared results_cache, so a per-image loop returns
    # the FIRST image's cached result for every other timestamp — silently
    # collapsing every slice to the first one (e.g. dropping the data of every
    # acquisition but the first). This is the same bug class fixed for apply /
    # array_apply / temporal apply_dimension; see the module-level warning.
    keys = list(data.keys())
    images = [data[k] for k in keys]

    # Stack to (time, bands, height, width), then move bands to the front so the
    # callback's band-wise ops (array_element indexes axis 0) operate on bands
    # while broadcasting across time. numpy.ma.stack (not numpy.stack) preserves
    # the per-image nodata masks. Mirrors _reduce_spectral_dimension_stack.
    stacked = numpy.ma.stack([img.array for img in images], axis=0)
    transposed = numpy.moveaxis(stacked, 1, 0)  # (bands, time, height, width)

    result_array = process(
        transposed,
        positional_parameters=positional_parameters,
        named_parameters=named_parameters,
    )

    if not isinstance(result_array, numpy.ndarray):
        raise ValueError(
            "The process must return a numpy array for spectral dimension processing"
        )

    # Map the result back to per-time (out_bands, height, width) slices.
    if result_array.ndim == 4:
        # (out_bands, time, height, width) -> (time, out_bands, height, width)
        per_time = numpy.moveaxis(result_array, 1, 0)
    elif result_array.ndim == 3:
        # (time, height, width) -> (time, 1, height, width): spectral dim reduced
        # to a single band.
        per_time = result_array[:, numpy.newaxis, :, :]
    else:
        raise ValueError(
            "The spectral process must return a 3D (time, height, width) or 4D "
            f"(out_bands, time, height, width) array, got shape {result_array.shape}."
        )

    if per_time.shape[0] != len(keys):
        raise ValueError(
            f"The spectral process changed the temporal dimension size "
            f"(expected {len(keys)}, got {per_time.shape[0]})."
        )

    result = {}
    for i, (key, img) in enumerate(zip(keys, images)):
        result[key] = ImageData(
            per_time[i],
            assets=img.assets,
            crs=img.crs,
            bounds=img.bounds,
            band_descriptions=img.band_names if img.band_names else [],
            metadata={
                "applied_dimension": "spectral",
            },
        )

    return RasterStack.from_images(result)


def xyz_to_bbox(
    data: Dict[str, Any],
    context: Optional[Any] = None,
) -> BoundingBox:
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
    bbox = BoundingBox(
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
    context: Optional[Any] = None,
) -> Dict:
    """Convert XYZ coordinates to tile information."""

    return {
        "x": x,
        "y": y,
        "z": z,
        "stage": stage,
        "context": context or {},
    }
