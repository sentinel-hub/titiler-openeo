"""titiler.openeo.processes Spatial."""

from typing import Callable, Dict, Optional, Union

from .data_model import ImageData, RasterStack

__all__ = ["resample_spatial"]


def resample_spatial(
    data: Union[RasterStack, ImageData],
    process: Callable,
    context: Optional[Dict] = None,
) -> Union[RasterStack, ImageData]:
    """Resample and warp the spatial dimensions of the raster at a given resolution."""
    positional_parameters = {"x": 0}
    named_parameters = {"context": context}

    def _reproject_img(img: ImageData):
        
        img.transform
        ds = ds.rio.reproject(
            dst_crs,
            shape=(tilesize, tilesize),
            transform=from_bounds(*tile_bounds, height=tilesize, width=tilesize),
            resampling=Resampling[reproject_method],
            nodata=nodata,
        )
        
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
        return _reproject_img(data)

    return {k: _reproject_img(img) for k, img in data.items()}
