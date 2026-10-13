"""titiler.openeo.processes focal operations."""

from typing import List, Union

import numpy

from ...errors import OpenEOException
from .data_model import ImageData, RasterStack

__all__ = ["apply_kernel"]

# openEO border modes -> numpy.pad modes.
BORDER_MODES = {
    "replicate": "edge",  # aaaaaa|abcdefgh|hhhhhh
    "reflect": "symmetric",  # fedcba|abcdefgh|hgfedc
    "reflect_pixel": "reflect",  # gfedcb|abcdefgh|gfedcb
    "wrap": "wrap",  # cdefgh|abcdefgh|abcdef
}


class KernelDimensionsUneven(OpenEOException):
    """Raised when a kernel dimension has an even number of elements."""

    def __init__(self) -> None:
        super().__init__(
            message="Each dimension of the kernel must have an uneven number of elements.",
            code="KernelDimensionsUneven",
            status_code=400,
        )


def _convolve(
    img: ImageData,
    kernel: numpy.ndarray,
    factor: float,
    border: Union[str, float],
    replace_invalid: float,
) -> ImageData:
    """Convolve each band of a single ImageData with the kernel."""
    dtype = "float64" if img.array.dtype == numpy.float64 else "float32"
    values = numpy.ma.masked_invalid(img.array.astype(dtype)).filled(replace_invalid)

    ky, kx = kernel.shape
    pad = ((0, 0), (ky // 2, ky // 2), (kx // 2, kx // 2))
    if isinstance(border, str):
        padded = numpy.pad(values, pad, mode=BORDER_MODES[border])
    else:
        padded = numpy.pad(values, pad, mode="constant", constant_values=border)

    # A true convolution flips the kernel. Sum the shifted planes one kernel
    # cell at a time, so the memory stays at one extra plane per band.
    flipped = kernel[::-1, ::-1]
    height, width = values.shape[1:]
    result = numpy.zeros_like(values)
    for i in range(ky):
        for j in range(kx):
            if flipped[i, j]:
                result += flipped[i, j] * padded[:, i : i + height, j : j + width]
    result *= factor

    return ImageData(
        numpy.ma.MaskedArray(result, mask=numpy.ma.getmaskarray(img.array)),
        assets=img.assets,
        crs=img.crs,
        bounds=img.bounds,
        cutline_mask=img.cutline_mask,
        metadata=img.metadata,
        band_names=img.band_names,
        band_descriptions=img.band_descriptions,
    )


def apply_kernel(
    data: RasterStack,
    kernel: List[List[float]],
    factor: float = 1,
    border: Union[str, float] = 0,
    replace_invalid: float = 0,
) -> RasterStack:
    """Apply a 2D convolution on the spatial dimensions of a raster data cube.

    The kernel is flipped (a true convolution, as ``scipy.ndimage.convolve``).
    The sum is multiplied by ``factor``. NaN, infinite and no-data values are
    replaced by ``replace_invalid`` before the convolution. No-data pixels of
    the input stay no-data in the output.

    Args:
        data: RasterStack to process
        kernel: Two-dimensional array of weights. The inner level aligns with
            ``x``, the outer level with ``y``. Each level must have an uneven
            number of elements.
        factor: Multiplier for each computed value
        border: How to extend the data at the borders: a number (constant
            fill), ``replicate``, ``reflect``, ``reflect_pixel`` or ``wrap``
        replace_invalid: Value used for NaN, infinite and no-data values

    Returns:
        RasterStack with the convolved values, on the input grid

    Raises:
        KernelDimensionsUneven: If a kernel dimension has an even size
    """
    weights = numpy.asarray(kernel, dtype="float64")
    if weights.ndim != 2:
        raise ValueError("The kernel must be a two-dimensional array of numbers.")
    if weights.shape[0] % 2 == 0 or weights.shape[1] % 2 == 0:
        raise KernelDimensionsUneven()
    if isinstance(border, str) and border not in BORDER_MODES:
        raise ValueError(
            f"Unsupported border '{border}'. Use a number or one of "
            f"{sorted(BORDER_MODES)}."
        )

    return data.map_images(
        lambda img: _convolve(img, weights, factor, border, replace_invalid)
    )
