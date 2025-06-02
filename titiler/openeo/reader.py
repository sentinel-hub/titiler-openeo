"""titiler-openeo custom reader."""

import time
import warnings
from typing import Any, Dict, List, Optional, Sequence, Tuple, Type, Union
from urllib.parse import urlparse

import attr
import rasterio
from morecantile import TileMatrixSet
from openeo_pg_parser_networkx.pg_schema import BoundingBox
from rasterio.errors import RasterioIOError
from rasterio.transform import array_bounds
from rasterio.warp import transform_bounds
from rio_tiler.constants import WEB_MERCATOR_TMS, WGS84_CRS
from rio_tiler.errors import (
    AssetAsBandError,
    ExpressionMixingWarning,
    InvalidAssetName,
    MissingAssets,
)
from rio_tiler.io import Reader
from rio_tiler.io.base import BaseReader, MultiBaseReader
from rio_tiler.models import ImageData
from rio_tiler.tasks import multi_arrays
from rio_tiler.types import AssetInfo, BBox, Indexes
from rio_tiler.utils import cast_to_sequence
from typing_extensions import TypedDict

from titiler.openeo.errors import (
    MixedCRSError,
    OutputLimitExceeded,
    ProcessParameterMissing,
)


class Dims(TypedDict):
    """Estimate Dimensions."""

    width: int
    height: int
    crs: rasterio.crs.CRS
    bbox: List[float]


@attr.s
class SimpleSTACReader(MultiBaseReader):
    """Simplified STAC Reader.

    Inputs should be in form of:
    ```json
    {
        "id": "IAMASTACITEM",
        "collection": "mycollection",
        "bbox": (0, 0, 10, 10),
        "assets": {
            "COG": {
                "href": "https://somewhereovertherainbow.io/cog.tif"
            }
        }
    }
    ```

    """

    input: Dict[str, Any] = attr.ib()

    tms: TileMatrixSet = attr.ib(default=WEB_MERCATOR_TMS)
    minzoom: int = attr.ib(default=None)
    maxzoom: int = attr.ib(default=None)

    assets: Sequence[str] = attr.ib(init=False)
    default_assets: Optional[Sequence[str]] = attr.ib(default=None)

    reader: Type[BaseReader] = attr.ib(default=Reader)
    reader_options: Dict = attr.ib(factory=dict)

    ctx: Any = attr.ib(default=rasterio.Env)

    def __attrs_post_init__(self) -> None:
        """Set reader spatial infos and list of valid assets."""
        self.bounds = self.input["bbox"]
        self.crs = WGS84_CRS  # Per specification STAC items are in WGS84

        if proj := self.input.get("proj"):
            crs_string = str(
                proj.get("code")
                or (f"epsg:{proj.get('epsg')}" if proj.get("epsg") else None)
                or proj.get("wkt")
            )
            if all(
                [
                    proj.get("transform"),
                    proj.get("shape"),
                    crs_string,
                ]
            ):
                self.height, self.width = proj.get("shape")
                self.transform = proj.get("transform")
                self.bounds = array_bounds(self.height, self.width, self.transform)
                self.crs = rasterio.crs.CRS.from_user_input(crs_string)

        self.minzoom = self.minzoom if self.minzoom is not None else self._minzoom
        self.maxzoom = self.maxzoom if self.maxzoom is not None else self._maxzoom

        self.assets = list(self.input["assets"])

        if not self.assets:
            raise MissingAssets(
                "No valid asset found. Asset's media types not supported"
            )

    def _parse_vrt_asset(self, asset: str) -> Tuple[str, Optional[str]]:
        if asset.startswith("vrt://") and asset not in self.assets:
            parsed = urlparse(asset)
            if not parsed.netloc:
                raise InvalidAssetName(
                    f"'{asset}' is not valid, couldn't find valid asset"
                )

            if parsed.netloc not in self.assets:
                raise InvalidAssetName(
                    f"'{parsed.netloc}' is not valid, should be one of {self.assets}"
                )

            return parsed.netloc, parsed.query

        return asset, None

    def _get_asset_info(self, asset: str) -> AssetInfo:
        """Validate asset names and return asset's url.

        Args:
            asset (str): STAC asset name.

        Returns:
            str: STAC asset href.

        """
        asset, vrt_options = self._parse_vrt_asset(asset)
        if asset not in self.assets:
            raise InvalidAssetName(
                f"{asset} is not valid. Should be one of {self.assets}"
            )

        asset_info = self.input["assets"][asset]
        info = AssetInfo(
            url=asset_info["href"],
            env={},
        )

        if media_type := asset_info.get("type"):
            info["media_type"] = media_type

        if header_size := asset_info.get("file:header_size"):
            info["env"]["GDAL_INGESTED_BYTES_AT_OPEN"] = header_size

        if bands := asset_info.get("raster:bands"):
            stats = [
                (b["statistics"]["minimum"], b["statistics"]["maximum"])
                for b in bands
                if {"minimum", "maximum"}.issubset(b.get("statistics", {}))
            ]
            if len(stats) == len(bands):
                info["dataset_statistics"] = stats

        if vrt_options:
            # Construct VRT url
            info["url"] = f"vrt://{info['url']}?{vrt_options}"

        return info

    # The regular STAC Reader doesn't have a `read` method
    def read(
        self,
        assets: Optional[Union[Sequence[str], str]] = None,
        expression: Optional[str] = None,
        asset_indexes: Optional[Dict[str, Indexes]] = None,
        asset_as_band: bool = False,
        **kwargs: Any,
    ) -> ImageData:
        """Read and merge previews from multiple assets.

        Args:
            assets (sequence of str or str, optional): assets to fetch info from.
            expression (str, optional): rio-tiler expression for the asset list (e.g. asset1/asset2+asset3).
            asset_indexes (dict, optional): Band indexes for each asset (e.g {"asset1": 1, "asset2": (1, 2,)}).
            kwargs (optional): Options to forward to the `self.reader.preview` method.

        Returns:
            rio_tiler.models.ImageData: ImageData instance with data, mask and tile spatial info.

        """
        assets = cast_to_sequence(assets)
        if assets and expression:
            warnings.warn(
                "Both expression and assets passed; expression will overwrite assets parameter.",
                ExpressionMixingWarning,
                stacklevel=2,
            )

        if expression:
            assets = self.parse_expression(expression, asset_as_band=asset_as_band)

        if not assets and self.default_assets:
            warnings.warn(
                f"No assets/expression passed, defaults to {self.default_assets}",
                UserWarning,
                stacklevel=2,
            )
            assets = self.default_assets

        if not assets:
            raise MissingAssets(
                "assets must be passed via `expression` or `assets` options, or via class-level `default_assets`."
            )

        asset_indexes = asset_indexes or {}

        # We fall back to `indexes` if provided
        indexes = kwargs.pop("indexes", None)

        def _reader(asset: str, **kwargs: Any) -> ImageData:
            idx = asset_indexes.get(asset) or indexes  # type: ignore

            asset_info = self._get_asset_info(asset)
            reader, options = self._get_reader(asset_info)

            with self.ctx(**asset_info.get("env", {})):
                with reader(
                    asset_info["url"],
                    tms=self.tms,
                    **{**self.reader_options, **options},
                ) as src:
                    data = src.preview(indexes=idx, **kwargs)

                    self._update_statistics(
                        data,
                        indexes=idx,
                        statistics=asset_info.get("dataset_statistics"),
                    )

                    metadata = data.metadata or {}
                    if m := asset_info.get("metadata"):
                        metadata.update(m)
                    data.metadata = {asset: metadata}

                    if asset_as_band:
                        if len(data.band_names) > 1:
                            raise AssetAsBandError(
                                "Can't use `asset_as_band` for multibands asset"
                            )
                        data.band_names = [asset]
                    else:
                        data.band_names = [f"{asset}_{n}" for n in data.band_names]

                    return data

        img = multi_arrays(assets, _reader, **kwargs)
        if expression:
            return img.apply_expression(expression)

        return img


def _validate_input_parameters(
    spatial_extent: BoundingBox,
    items: List[Dict],
    bands: Optional[list[str]],
) -> None:
    """Validate required input parameters."""
    if not spatial_extent:
        raise ProcessParameterMissing("spatial_extent")
    if not items:
        raise ProcessParameterMissing("items")
    if not bands:
        raise ProcessParameterMissing("bands")


def _get_item_resolutions(
    item: Dict,
    src_dst: SimpleSTACReader,
    spatial_extent: BoundingBox,
) -> tuple[list[float], list[float]]:
    """Get x and y resolutions from a STAC item."""
    x_resolutions = []
    y_resolutions = []

    if src_dst.transform:
        x_resolutions.append(abs(src_dst.transform.a))
        y_resolutions.append(abs(src_dst.transform.e))
    else:
        for _, asset in item.get("assets", {}).items():
            if asset_transform := asset.get("proj:transform"):
                x_resolutions.append(abs(asset_transform[0]))
                y_resolutions.append(abs(asset_transform[4]))
            elif asset_shape := asset.get("proj:shape"):
                if asset_shape[0] > 0 and asset_shape[1] > 0:
                    x_resolutions.append(
                        abs(
                            (spatial_extent.east - spatial_extent.west) / asset_shape[0]
                        )
                    )
                    y_resolutions.append(
                        abs(
                            (spatial_extent.north - spatial_extent.south)
                            / asset_shape[1]
                        )
                    )
            else:
                x_resolutions.append(1024)
                y_resolutions.append(1024)

    return x_resolutions, y_resolutions


def _reproject_resolution(
    item_crs: rasterio.crs.CRS,
    crs: rasterio.crs.CRS,
    bbox: List[float],
    x_resolution: Optional[float],
    y_resolution: Optional[float],
) -> tuple[Optional[float], Optional[float]]:
    """Reproject resolution if CRS differs."""
    if not (item_crs and item_crs != crs):
        return x_resolution, y_resolution

    center_x = (bbox[0] + bbox[2]) / 2
    center_y = (bbox[1] + bbox[3]) / 2
    src_box = [
        center_x,
        center_y,
        center_x + x_resolution if x_resolution else 0,
        center_y + y_resolution if y_resolution else 0,
    ]
    dst_box = transform_bounds(item_crs, crs, *src_box, densify_pts=21)

    return (
        abs(dst_box[2] - dst_box[0]) if x_resolution else None,
        abs(dst_box[3] - dst_box[1]) if y_resolution else None,
    )


def _calculate_dimensions(
    bbox: List[float],
    x_resolution: Optional[float],
    y_resolution: Optional[float],
    width: Optional[int] = None,
    height: Optional[int] = None,
) -> tuple[int, int]:
    """Calculate output dimensions."""
    if width and height:
        return width, height

    if x_resolution and y_resolution:
        width = int(round((bbox[2] - bbox[0]) / x_resolution))
        height = int(round((bbox[3] - bbox[1]) / y_resolution))
    else:
        width = 1024
        height = 1024

    return width, height


def _check_pixel_limit(
    width: Optional[int],
    height: Optional[int],
    items: List[Dict],
) -> None:
    """Check if pixel count exceeds maximum allowed.

    For mosaics, items with the same datetime are counted only once since they
    will be combined into a single mosaic.
    """
    from .settings import ProcessingSettings

    processing_settings = ProcessingSettings()

    width_int = int(width or 0)
    height_int = int(height or 0)

    # Group items by datetime to avoid double counting mosaic items
    datetimes = set()
    for item in items:
        dt = item.get("properties", {}).get("datetime")
        if dt:
            datetimes.add(dt)
        else:
            # If no datetime, treat as unique item
            datetimes.add(id(item))

    # Use number of unique datetimes instead of total items
    pixel_count = width_int * height_int * len(datetimes)
    if pixel_count > processing_settings.max_pixels:
        raise OutputLimitExceeded(
            width_int,
            height_int,
            processing_settings.max_pixels,
            items_count=len(datetimes),
        )


def _estimate_output_dimensions(
    items: List[Dict],
    spatial_extent: BoundingBox,
    bands: Optional[list[str]],
    width: Optional[int] = None,
    height: Optional[int] = None,
) -> Dims:
    """
    Estimate output dimensions based on items and spatial extent.

    Args:
        items: List of STAC items
        spatial_extent: Bounding box for the output
        bands: List of band names to include
        width: Optional user-specified width
        height: Optional user-specified height

    Returns:
        Dictionary containing:
            - width: Estimated or specified width
            - height: Estimated or specified height
            - crs: Target CRS to use
            - bbox: Bounding box as a list [west, south, east, north]
    """
    _validate_input_parameters(spatial_extent, items, bands)

    # Extract CRS and resolution information from items
    item_crs: rasterio.crs.CRS = None
    all_x_resolutions = []
    all_y_resolutions = []

    for item in items:
        with SimpleSTACReader(item) as src_dst:
            if item_crs is None:
                item_crs = src_dst.crs
            elif item_crs != src_dst.crs:
                raise MixedCRSError(
                    found_crs=str(src_dst.crs), expected_crs=str(item_crs)
                )

            x_res, y_res = _get_item_resolutions(item, src_dst, spatial_extent)
            all_x_resolutions.extend(x_res)
            all_y_resolutions.extend(y_res)

    # Get the highest resolution (smallest pixel size)
    x_resolution = min(all_x_resolutions) if all_x_resolutions else None
    y_resolution = min(all_y_resolutions) if all_y_resolutions else None

    # Get target CRS and bounds
    crs = rasterio.crs.CRS.from_user_input(spatial_extent.crs or "epsg:4326")
    bbox = [
        spatial_extent.west,
        spatial_extent.south,
        spatial_extent.east,
        spatial_extent.north,
    ]

    # Reproject resolution if needed
    x_resolution, y_resolution = _reproject_resolution(
        item_crs, crs, bbox, x_resolution, y_resolution
    )

    # Calculate dimensions
    width, height = _calculate_dimensions(
        bbox, x_resolution, y_resolution, width, height
    )

    # Check pixel limit
    _check_pixel_limit(width, height, items)

    return Dims(
        width=width,  # type: ignore
        height=height,  # type: ignore
        crs=crs,
        bbox=bbox,
    )


def _reader(item: Dict[str, Any], bbox: BBox, **kwargs: Any) -> ImageData:
    """
    Read a STAC item and return an ImageData object.

    Args:
        item: STAC item dictionary
        bbox: Bounding box to read
        **kwargs: Additional keyword arguments to pass to the reader

    Returns:
        ImageData object
    """
    max_retries = 4
    retry_delay = 1.0  # seconds
    retries = 0

    while True:
        try:
            with SimpleSTACReader(item) as src_dst:
                return src_dst.part(bbox, **kwargs)
        except RasterioIOError as e:
            retries += 1
            if retries >= max_retries:
                # If we've reached max retries, re-raise the exception
                raise
            # Log the error and retry after a delay
            print(
                f"RasterioIOError encountered: {str(e)}. Retrying in {retry_delay} seconds... (Attempt {retries}/{max_retries})"
            )
            time.sleep(retry_delay)
            # Increase delay for next retry (exponential backoff)
            retry_delay *= 2
