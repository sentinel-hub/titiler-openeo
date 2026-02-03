"""titiler.processes.implementations arrays."""

from datetime import datetime
from typing import Optional, Union

import numpy
from numpy.typing import ArrayLike
from rio_tiler.models import ImageData

from .core import process
from .data_model import RasterStack

__all__ = [
    "array_element",
    "to_image",
    "array_create",
    "create_data_cube",
    "add_dimension",
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
