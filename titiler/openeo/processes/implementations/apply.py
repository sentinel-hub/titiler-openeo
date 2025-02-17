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

    if isinstance(data, ImageData):
        return _process_img(data)

    return {k: _process_img(img) for k, img in data.items()}
