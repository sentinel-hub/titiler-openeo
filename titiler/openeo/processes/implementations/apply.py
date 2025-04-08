"""titiler.openeo.processes Apply."""

from typing import Callable, Dict, Optional

from .data_model import ImageData, RasterStack

__all__ = ["apply"]


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
