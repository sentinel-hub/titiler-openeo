"""titiler.openeo.processes Apply."""

from typing import Callable, Dict, Optional, Union

from .data_model import ImageData, RasterStack

__all__ = ["apply"]


def apply(
    data: Union[RasterStack, ImageData],
    process: Callable,
    context: Optional[Dict] = None,
) -> Union[RasterStack, ImageData]:
    """Apply process on Data."""
    context = context or {}

    def _process_img(img: ImageData):
        return ImageData(
            process(x=img.array, **context),  # type: ignore
            assets=img.assets,
            crs=img.crs,
            bounds=img.bounds,
            band_names=img.band_names,
        )

    if isinstance(data, ImageData):
        return _process_img(data)

    return {k: _process_img(img) for k, img in data.items()}
