"""titiler.processes.implementations arrays."""

from typing import Optional, Union

import numpy
from numpy.typing import ArrayLike
from rio_tiler.models import ImageData

from .core import process
from .data_model import LazyRasterStack, RasterStack

__all__ = [
    "array_element",
    "to_image",
    "array_create",
    "create_data_cube",
    "add_dimension",
]


def array_element(
    data: Union[ArrayLike, RasterStack, LazyRasterStack],
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
    if index is not None and index < 0:
        raise IndexError(f"Index value must be >= 0, {index}")
    if label is not None and label not in data.keys():
        raise KeyError(f"Label {label} not found in data: {data.keys()}")
    if index is None and label is None:
        raise ValueError("Either index or label must be provided")

    # Handle RasterStack
    if isinstance(data, dict):
        if index is not None:
            array_dict = {
                k: numpy.take(v.array, index, axis=0) for k, v in data.items()
            }
        if label is not None:
            array_dict = {k: v.array for k, v in data.items() if k == label}
        # return a multi-dimensional array
        return numpy.stack(list(array_dict.values()), axis=0)

    # Handle regular arrays
    elif isinstance(data, ImageData):
        return numpy.take(data.array, index, axis=0)
    else:
        return numpy.take(data, index, axis=0)


def to_image(data: Union[numpy.ndarray, numpy.ma.MaskedArray]) -> RasterStack:
    """Create a RasterStack from an array."""
    return {"data": ImageData(data)}


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

    # Handle both numpy arrays and other array-like inputs
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
    return {}


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
    if name in data:
        raise ValueError(f"A dimension with name '{name}' already exists")

    if type == "spatial":
        raise ValueError(
            "Cannot add spatial dimensions - they are inherent to the raster data"
        )

    # For empty data cube, we can add any non-spatial dimension
    if not data:
        # Create an ImageData with a default shape for XYZ tiles
        data[name] = ImageData(
            numpy.ma.masked_array(array_create()),
            metadata={"dimension": name, "label": label, "type": type},
            bounds=(0, 0, 1, 1),  # Default bounds for a single pixel
            crs="EPSG:4326",  # Default CRS
        )
        return data

    # For non-empty data cube, we need to ensure the new dimension is compatible
    # with existing spatial dimensions
    first_image = next(iter(data.values()))
    empty_array = numpy.ma.masked_array(
        numpy.zeros((1, first_image.height, first_image.width)),
        mask=True,  # All values are masked initially
    )

    data[name] = ImageData(
        empty_array,
        metadata={"dimension": name, "label": label, "type": type},
        crs=first_image.crs,
        bounds=first_image.bounds,
    )

    return data
