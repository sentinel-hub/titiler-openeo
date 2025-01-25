"""titiler.openeo.processes Spatial."""

from typing import Tuple, Union

from pyproj import CRS
from rasterio.warp import Resampling

from .data_model import ImageData, RasterStack

__all__ = ["resample_spatial"]


def resample_spatial(
    data: Union[RasterStack, ImageData],
    projection: Union[int, str],
    resolution: Union[float, Tuple[float, float]],
    align: str,
    method: str = "nearest",
) -> Union[RasterStack, ImageData]:
    """Resample and warp the spatial dimensions of the raster at a given resolution."""

    def _reproject_img(
        img: ImageData, dst_crs: CRS, resolution: int, method: str
    ) -> ImageData:
        # align is not yet implemented
        if align is not None:
            raise NotImplementedError(
                "resample_spatial: align parameter is not yet implemented"
            )

        dst_crs = CRS(projection)
        # map resampling method to rio-tiler method using a dictionary
        resampling_method: Resampling = {
            "nearest": Resampling.nearest,
            "bilinear": Resampling.bilinear,
            "cubic": Resampling.cubic,
            "cubicspline": Resampling.cubic_spline,
            "lanczos": Resampling.lanczos,
            "average": Resampling.average,
            "mode": Resampling.mode,
            "max": Resampling.max,
            "min": Resampling.min,
            "med": Resampling.med,
            "q1": Resampling.q1,
            "q3": Resampling.q3,
            "sum": Resampling.sum,
            "rms": Resampling.rms,
            "near": None,
        }[method]

        res = resolution if isinstance(resolution, tuple) else (resolution, resolution)

        # reproject the image
        return img.reproject(dst_crs, res, resampling_method)

    """ Get destination CRS from parameters """
    dst_crs = CRS.from_epsg(projection)

    if isinstance(data, ImageData):
        return _reproject_img(data, dst_crs, resolution, method)

    return {k: _reproject_img(img) for k, img in data.items()}
