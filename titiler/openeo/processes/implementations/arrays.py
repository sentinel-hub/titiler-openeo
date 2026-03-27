"""titiler.processes.implementations arrays."""

import logging
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Union

import numpy
from numpy.typing import ArrayLike
from rio_tiler.models import ImageData
from rio_tiler.utils import resize_array

from .core import process
from .data_model import RasterStack

__all__ = [
    "array_element",
    "to_image",
    "array_create",
    "create_data_cube",
    "add_dimension",
    "merge_cubes",
]


@process
def array_element(
    data: Union[ArrayLike, RasterStack],
    index: Optional[int] = None,
    label: Optional[str] = None,
) -> ArrayLike:
    """Return element from array.

    Args:
        data: Array or RasterStack to extract element from
        index: Index of the element to extract
        label: Label of the element to extract (for RasterStack)

    Returns:
        The element at the specified index
    """
    # Basic validation
    if index is not None and index < 0:
        raise IndexError(f"Index value must be >= 0, {index}")
    if index is None and label is None:
        raise ValueError("Either index or label must be provided")

    # Label is only supported for RasterStack/dict types
    if label is not None and not isinstance(data, dict):
        raise ValueError(
            "Label parameter is only supported for RasterStack/dict data types"
        )

    # Validate label exists in dict (do this after type check)
    if label is not None and isinstance(data, dict) and label not in data.keys():
        raise KeyError(f"Label {label} not found in data: {data.keys()}")

    # Handle RasterStack
    if isinstance(data, dict):
        if index is not None:
            array_dict = {
                k: numpy.take(v.array, index, axis=0) for k, v in data.items()
            }
        if label is not None:
            array_dict = {k: v.array for k, v in data.items() if k == label}
        # return a multi-dimensional array
        # For RasterStack, this will execute all tasks when stacking is needed
        # This is expected behavior for array operations
        return numpy.stack(list(array_dict.values()), axis=0)

    # Handle regular arrays (index must be provided at this point)
    if index is None:
        raise ValueError("Index must be provided for non-dict data types")

    # Get the array to work with
    array_data = data.array if isinstance(data, ImageData) else data

    # Convert to numpy array to ensure we have a shape attribute
    array_data = numpy.asarray(array_data)

    # Handle 0-dimensional arrays (scalars)
    # If index is 0, return the scalar value itself
    # Otherwise raise an error since we can't index into a scalar
    if array_data.ndim == 0:
        if index == 0:
            return array_data.item()  # Return the scalar value
        else:
            raise IndexError(
                f"Cannot index scalar (0-dimensional array) with index {index}"
            )

    # Check bounds
    if index >= array_data.shape[0]:
        raise IndexError(
            f"Index {index} is out of bounds for axis 0 with size {array_data.shape[0]}"
        )

    return numpy.take(array_data, index, axis=0)


def to_image(data: Union[numpy.ndarray, numpy.ma.MaskedArray]) -> RasterStack:
    """Create a RasterStack from an array."""
    return RasterStack.from_images({datetime.now(): ImageData(data)})


@process
def array_create(data: Optional[ArrayLike] = None, repeat: int = 1) -> ArrayLike:
    """Creates a new array.

    Args:
        data: A (native) array to fill the newly created array with. Defaults to an empty array.
        repeat: The number of times the array is repeatedly added. Defaults to 1.

    Returns:
        A numpy array containing the repeated data.
    """
    if data is None:
        # Return a default array for XYZ tiles
        return numpy.empty((1, 1, 1), dtype=numpy.uint8)

    # If data is a list/tuple of arrays, stack them along axis 0
    if isinstance(data, (list, tuple)):
        # Convert each element to array and stack them
        arrays = [numpy.asanyarray(item) for item in data]
        # Stack along first axis - this creates (n_elements, *spatial_dims)
        return numpy.stack(arrays, axis=0)

    # Handle single array input
    arr = numpy.asanyarray(data)

    # # check that the array is no more than 2D
    # if arr.ndim > 2:
    #     raise ValueError("Array must be 1D or 2D")
    # if arr.shape[0] == 0 or arr.shape[1] == 0:
    #     raise ValueError("Array must not be empty")
    # # conceptually, repeating a 2d array is like stacking it in the third dimension
    # # so we cannot repeat it in this function
    # if repeat != 1:
    #     raise ValueError("Cannot repeat a 2D array")

    return arr


@process
def create_data_cube() -> RasterStack:
    """Creates a new data cube without dimensions.

    Returns:
        An empty data cube (RasterStack) with no dimensions.
    """
    # Return empty dict - type: ignore needed as RasterStack.from_images requires non-empty
    return {}  # type: ignore[return-value]


@process
def add_dimension(
    data: RasterStack, name: str, label: Union[str, float], type: str = "other"
) -> RasterStack:
    """Adds a new named dimension to the data cube.

    Args:
        data: A data cube to add the dimension to.
        name: Name for the dimension.
        label: A dimension label.
        type: The type of dimension. Defaults to 'other'.

    Returns:
        The data cube with a newly added dimension.

    Raises:
        ValueError: If a dimension with the specified name already exists.
        ValueError: If trying to add a spatial dimension (not supported).
    """
    # Check if dimension name conflicts with existing timestamps (converted to string)
    existing_keys = [str(k) for k in data.keys()]
    if name in existing_keys:
        raise ValueError(f"A dimension with name '{name}' already exists")

    if type == "spatial":
        raise ValueError(
            "Cannot add spatial dimensions - they are inherent to the raster data"
        )

    # For empty data cube, we can add any non-spatial dimension
    if not data:
        # Create an ImageData with a default shape for XYZ tiles
        new_data = {
            datetime.now(): ImageData(
                numpy.ma.masked_array(array_create()),
                metadata={"dimension": name, "label": label, "type": type},
                bounds=(0, 0, 1, 1),  # Default bounds for a single pixel
                crs="EPSG:4326",  # Default CRS
            )
        }
        return RasterStack.from_images(new_data)

    # For non-empty data cube, we need to ensure the new dimension is compatible
    # with existing spatial dimensions
    # Use get_image_refs() to get metadata WITHOUT loading pixel data
    image_refs = data.get_image_refs()
    if not image_refs:
        raise ValueError("No image refs available for metadata")
    _first_key, first_ref = image_refs[0]
    empty_array = numpy.ma.masked_array(
        numpy.zeros((1, first_ref.height, first_ref.width)),
        mask=True,  # All values are masked initially
    )

    # Copy existing data and add the new dimension with a new timestamp
    new_data = dict(data.items())
    new_data[datetime.now()] = ImageData(
        empty_array,
        metadata={"dimension": name, "label": label, "type": type},
        crs=first_ref.crs,
        bounds=first_ref.bounds,
    )

    return RasterStack.from_images(new_data)


class OverlapResolverMissing(Exception):
    """Raised when overlapping data cubes have no overlap resolver specified."""

    def __init__(self) -> None:
        super().__init__(
            "Overlapping data cubes, but no overlap resolver has been specified."
        )


def _resize_image_to_match(
    img: ImageData,
    target_height: int,
    target_width: int,
) -> ImageData:
    """Resize an ImageData to match target spatial dimensions.

    Args:
        img: Source image to resize
        target_height: Target height in pixels
        target_width: Target width in pixels

    Returns:
        Resized ImageData (or original if already matching)
    """
    if img.height == target_height and img.width == target_width:
        return img

    logging.warning(
        "Resizing cube2 image from %dx%d to %dx%d to match cube1 spatial dimensions",
        img.width,
        img.height,
        target_width,
        target_height,
    )

    resized_data = resize_array(img.array.data, target_height, target_width)
    resized_mask = resize_array(
        img.array.mask.astype("uint8")
        if isinstance(img.array.mask, numpy.ndarray)
        else numpy.zeros_like(img.array.data, dtype="uint8"),
        target_height,
        target_width,
    ).astype("bool")

    return ImageData(
        numpy.ma.MaskedArray(resized_data, mask=resized_mask),
        assets=img.assets,
        crs=img.crs,
        bounds=img.bounds,
        band_descriptions=img.band_descriptions or [],
        metadata=img.metadata or {},
    )


def _merge_images_bands(
    img1: ImageData,
    img2: ImageData,
    overlap_resolver: Optional[Callable],
    context: Optional[Dict[str, Any]] = None,
) -> ImageData:
    """Merge two ImageData objects along the band dimension.

    Handles three cases:
    1. Disjoint bands: concatenate arrays
    2. Overlapping bands with resolver: apply resolver to overlapping, concatenate rest
    3. No band names: treat all pixels as overlapping

    Args:
        img1: Image from cube1
        img2: Image from cube2 (already resized to match img1 spatial dims)
        overlap_resolver: Function to resolve overlapping values
        context: Additional context for the overlap resolver

    Returns:
        Merged ImageData

    Raises:
        OverlapResolverMissing: When overlap exists but no resolver was provided
    """
    bands1 = list(img1.band_descriptions) if img1.band_descriptions else []
    bands2 = list(img2.band_descriptions) if img2.band_descriptions else []

    # If neither image has band names, all data overlaps
    if not bands1 and not bands2:
        if overlap_resolver is None:
            raise OverlapResolverMissing()
        resolved = overlap_resolver(x=img1.array, y=img2.array, context=context)
        if not isinstance(resolved, (numpy.ndarray, numpy.ma.MaskedArray)):
            resolved = numpy.asarray(resolved)
        return ImageData(
            resolved,
            assets=(img1.assets or []) + (img2.assets or []),
            crs=img1.crs,
            bounds=img1.bounds,
            band_descriptions=bands1,
            metadata=img1.metadata or {},
        )

    # Find overlapping and unique bands
    set1 = set(bands1)
    set2 = set(bands2)
    overlapping = set1 & set2
    unique_to_2 = [b for b in bands2 if b not in set1]  # preserve order from cube2

    if overlapping and overlap_resolver is None:
        raise OverlapResolverMissing()

    # Start building the result from cube1's bands (preserving order)
    result_arrays: List[numpy.ndarray] = []
    result_band_names: List[str] = []

    for idx, band_name in enumerate(bands1):
        band_data_1 = img1.array[idx : idx + 1]  # shape (1, h, w)

        if band_name in overlapping:
            # Find matching band in cube2
            assert overlap_resolver is not None  # guarded above
            idx2 = bands2.index(band_name)
            band_data_2 = img2.array[idx2 : idx2 + 1]  # shape (1, h, w)
            resolved = overlap_resolver(x=band_data_1, y=band_data_2, context=context)
            if not isinstance(resolved, (numpy.ndarray, numpy.ma.MaskedArray)):
                resolved = numpy.asarray(resolved)
            # Ensure 3D shape
            if resolved.ndim == 2:
                resolved = resolved[numpy.newaxis, :, :]
            result_arrays.append(resolved)
        else:
            result_arrays.append(band_data_1)
        result_band_names.append(band_name)

    # Append bands unique to cube2 (preserving cube2 order)
    for band_name in unique_to_2:
        idx2 = bands2.index(band_name)
        result_arrays.append(img2.array[idx2 : idx2 + 1])
        result_band_names.append(band_name)

    merged_array = numpy.ma.concatenate(result_arrays, axis=0)

    return ImageData(
        merged_array,
        assets=(img1.assets or []) + (img2.assets or []),
        crs=img1.crs,
        bounds=img1.bounds,
        band_descriptions=result_band_names,
        metadata=img1.metadata or {},
    )


@process
def merge_cubes(
    cube1: RasterStack,
    cube2: RasterStack,
    overlap_resolver: Optional[Callable] = None,
    context: Optional[Dict[str, Any]] = None,
) -> RasterStack:
    """Merge two compatible data cubes.

    The data cubes must share compatible spatial dimensions. Temporal and band
    dimensions are merged according to the openEO specification:

    - Timestamps unique to either cube are included as-is
    - Overlapping timestamps with disjoint bands are concatenated
    - Overlapping timestamps with overlapping bands require an overlap_resolver
    - Spatial dimensions of cube2 are resampled to match cube1 if needed

    Args:
        cube1: The base data cube.
        cube2: The other data cube to merge with cube1.
        overlap_resolver: A reducer function that resolves conflicts when data
            overlaps. Called with (x=value_from_cube1, y=value_from_cube2).
            Required when there is overlap. Default is None.
        context: Additional data to be passed to the overlap resolver.

    Returns:
        The merged data cube as a RasterStack.

    Raises:
        OverlapResolverMissing: When overlapping data exists but no resolver
            was provided.
    """
    # Handle empty cubes
    if not cube1 and not cube2:
        return {}  # type: ignore[return-value]
    if not cube1:
        return cube2
    if not cube2:
        return cube1

    # Get reference spatial dimensions from cube1 (cube1 is the target for resampling)
    ref_img = cube1.first
    target_height = ref_img.height
    target_width = ref_img.width

    keys1 = set(cube1.keys())
    keys2 = set(cube2.keys())
    all_keys = sorted(keys1 | keys2)

    merged_images: Dict[datetime, ImageData] = {}

    for key in all_keys:
        in_cube1 = key in keys1
        in_cube2 = key in keys2

        if in_cube1 and not in_cube2:
            # Timestamp only in cube1
            merged_images[key] = cube1[key]

        elif in_cube2 and not in_cube1:
            # Timestamp only in cube2 - resize to match cube1 spatial dims
            img2 = cube2[key]
            merged_images[key] = _resize_image_to_match(
                img2, target_height, target_width
            )

        else:
            # Timestamp in both cubes - merge bands
            img1 = cube1[key]
            img2 = _resize_image_to_match(cube2[key], target_height, target_width)
            merged_images[key] = _merge_images_bands(
                img1, img2, overlap_resolver, context
            )

    return RasterStack.from_images(merged_images)
