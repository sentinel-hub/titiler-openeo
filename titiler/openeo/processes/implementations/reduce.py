"""titiler.openeo processed reduce."""

import warnings
from typing import List, Literal, Optional

import numpy
from rasterio.crs import CRS
from rio_tiler.models import ImageData
from rio_tiler.mosaic.methods import PixelSelectionMethod
from rio_tiler.types import BBox
from rio_tiler.utils import resize_array

from .data_model import RasterStack

__all__ = ["apply_pixel_selection"]

pixel_methods = Literal[
    "first",
    "highest",
    "lowest",
    "mean",
    "median",
    "stdev",
    "lastbandlow",
    "lastbandhight",
    "count",
]


def apply_pixel_selection(
    data: RasterStack,
    pixel_selection: str = "first",
) -> ImageData:
    """Apply PixelSelection method on a RasterStack."""
    pixsel_method = PixelSelectionMethod[pixel_selection].value()

    assets_used: List = []
    crs: Optional[CRS]
    bounds: Optional[BBox]
    band_names: List[str]

    for datetime, img in data.items():
        # On the first Image we set the properties
        if len(assets_used) == 0:
            crs = img.crs
            bounds = img.bounds
            band_names = img.band_names
            pixsel_method.cutline_mask = img.cutline_mask
            pixsel_method.width = img.width
            pixsel_method.height = img.height
            pixsel_method.count = img.count

        assert (
            img.count == pixsel_method.count
        ), "Assets HAVE TO have the same number of bands"

        if any(
            [
                img.width != pixsel_method.width,
                img.height != pixsel_method.height,
            ]
        ):
            warnings.warn(
                "Cannot concatenate images with different size. Will resize using fist asset width/heigh",
                UserWarning,
                stacklevel=2,
            )
            h = pixsel_method.height
            w = pixsel_method.width
            pixsel_method.feed(
                numpy.ma.MaskedArray(
                    resize_array(img.array.data, h, w),
                    mask=resize_array(img.array.mask * 1, h, w).astype("bool"),
                )
            )

        else:
            pixsel_method.feed(img.array)

        assets_used.append(datetime)

        if pixsel_method.is_done and pixsel_method.data is not None:
            return ImageData(
                pixsel_method.data,
                assets=assets_used,
                crs=crs,
                bounds=bounds,
                band_names=band_names,
                metadata={
                    "pixel_selection_method": pixel_selection,
                },
            )

    if pixsel_method.data is None:
        raise ValueError("Method returned an empty array")

    return ImageData(
        pixsel_method.data,
        assets=assets_used,
        crs=crs,
        bounds=bounds,
        band_names=band_names,
        metadata={
            "pixel_selection_method": pixel_selection,
        },
    )
