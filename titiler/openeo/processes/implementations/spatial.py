"""titiler.openeo.processes Spatial."""

from typing import Tuple, Union

from pyproj import CRS
from rio_tiler.types import WarpResampling

from .data_model import ImageData, RasterStack

__all__ = ["resample_spatial"]


def resample_spatial(
    data: Union[RasterStack, ImageData],
    projection: Union[int, str],
    resolution: Union[float, Tuple[float, float]],
    align: str,
    method: WarpResampling = "nearest",
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

        dst_crs = CRS.from_user_input(projection)
        # map resampling method to rio-tiler method using a dictionary
        resampling_method: WarpResampling = {
            "nearest": WarpResampling.nearest,
            "bilinear": WarpResampling.bilinear,
            "cubic": WarpResampling.cubic,
            "cubicspline": WarpResampling.cubic_spline,
            "lanczos": WarpResampling.lanczos,
            "average": WarpResampling.average,
            "mode": WarpResampling.mode,
            "max": None,
            "min": None,
            "med": None,
            "q1": None,
            "q3": None,
            "sum": WarpResampling.sum,
            "rms": WarpResampling.rms,
            "near": None,
        }[method]

        if resampling_method is None:
            raise ValueError(f"Unsupported resampling method: {method}")

        res = resolution if isinstance(resolution, tuple) else (resolution, resolution)

        # reproject the image
        return img.reproject(
            dst_crs, resolution=res, reproject_method=resampling_method
        )

    """ Get destination CRS from parameters """
    dst_crs = CRS.from_epsg(projection)

    if isinstance(data, ImageData):
        return _reproject_img(data, dst_crs, resolution, method)

    return {k: _reproject_img(img) for k, img in data.items()}
