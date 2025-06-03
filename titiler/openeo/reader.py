"""titiler-openeo custom reader."""

import time
import warnings
from typing import Any, Dict, List, Optional, Sequence, Tuple, Type, Union
from urllib.parse import urlparse

from affine import Affine
from shapely.geometry import box
import attr
import pystac
import rasterio
from morecantile import TileMatrixSet
from pystac.extensions.projection import ProjectionExtension
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

from titiler.openeo.errors import MixedCRSError, OutputLimitExceeded
from titiler.openeo.models import SpatialExtent


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
    item: pystac.Item = attr.ib(init=False)

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
        # Convert input to pystac.Item
        self.item = pystac.Item.from_dict(self.input)

        # Get bounding box and default CRS
        self.bounds = self.item.bbox
        self.crs = WGS84_CRS  # Default to WGS84

        # Get projection information using STAC extension
        if ProjectionExtension.has_extension(self.item):
            proj_ext = ProjectionExtension.ext(self.item)

            # Set CRS if available
            if proj_ext.epsg:
                self.crs = rasterio.crs.CRS.from_epsg(proj_ext.epsg)
            elif proj_ext.wkt2:
                self.crs = rasterio.crs.CRS.from_wkt(proj_ext.wkt2)

            # Set transform and shape if available
            if proj_ext.transform and proj_ext.shape:
                self.height, self.width = proj_ext.shape
                self.transform = Affine(proj_ext.transform[0], proj_ext.transform[1], proj_ext.transform[2],
                                        proj_ext.transform[3], proj_ext.transform[4], proj_ext.transform[5])
                # Update bounds if we have both transform and shape
                self.bounds = array_bounds(self.height, self.width, self.transform)
            elif proj_ext.transform:
                self.transform = proj_ext.transform
            elif proj_ext.shape:
                self.height, self.width = proj_ext.shape

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
        """Validate asset names and return asset's url and metadata.

        Args:
            asset (str): STAC asset name.

        Returns:
            AssetInfo: Asset URL and metadata.
        """
        asset, vrt_options = self._parse_vrt_asset(asset)
        if asset not in self.assets:
            raise InvalidAssetName(
                f"{asset} is not valid. Should be one of {self.assets}"
            )

        # Convert to pystac Item and get asset information
        stac_item = pystac.Item.from_dict(self.input)
        pystac_asset = stac_item.assets[asset]

        info = AssetInfo(
            url=pystac_asset.href,
            env={},
        )

        # Add media type if available
        if pystac_asset.media_type:
            info["media_type"] = pystac_asset.media_type

        # Add any asset-specific metadata
        if pystac_asset.extra_fields:
            # Handle file:header_size
            if header_size := pystac_asset.extra_fields.get("file:header_size"):
                info["env"]["GDAL_INGESTED_BYTES_AT_OPEN"] = header_size

            # Handle raster:bands statistics
            if bands := pystac_asset.extra_fields.get("raster:bands"):
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


def _get_assets_resolutions(
    item: Dict,
    src_dst: SimpleSTACReader,
    bands: Optional[list[str]] = None,
) -> Dict[str, tuple[float, float, rasterio.crs.CRS]]:
    """Get x and y resolutions and CRS for each band from STAC assets.

    Args:
        item: STAC item dictionary
        src_dst: SimpleSTACReader instance
        bands: Optional list of band names to filter assets

    Returns:
        Dictionary mapping band names to (x_resolution, y_resolution, crs) tuples
    """
    band_resolutions = {}

    # Convert item to pystac.Item to use projection extension
    stac_item = pystac.Item.from_dict(item)

    # If bands specified, only process those bands
    assets_to_process = set(bands) if bands else set(stac_item.get_assets().keys())

    for band_name in assets_to_process:
        if band_name not in item.get("assets", {}):
            continue

        asset = stac_item.assets[band_name]
        x_res = None
        y_res = None
        asset_crs = None

        # Get asset-level projection information
        if ProjectionExtension.has_extension(stac_item):
            asset_proj_ext = ProjectionExtension.ext(asset)

            if asset_proj_ext:
                # Try to get asset-level CRS
                if asset_proj_ext.epsg:
                    asset_crs = rasterio.crs.CRS.from_epsg(asset_proj_ext.epsg)
                elif asset_proj_ext.wkt2:
                    asset_crs = rasterio.crs.CRS.from_wkt(asset_proj_ext.wkt2)

                # Try to get transform and shape for resolution
                if asset_proj_ext.transform:
                    x_res = abs(asset_proj_ext.transform[0])
                    y_res = abs(asset_proj_ext.transform[4])
                elif asset_proj_ext.shape:
                    bbox = asset.get("bbox", item["bbox"])
                    shape = asset_proj_ext.shape
                    if shape[0] > 0 and shape[1] > 0:
                        x_res = abs((bbox[2] - bbox[0]) / shape[0])
                        y_res = abs((bbox[3] - bbox[1]) / shape[1])

        # Fall back to item-level CRS if needed
        if asset_crs is None:
            asset_crs = src_dst.crs

        # Use item transform if no asset-level resolution available
        if x_res is None and src_dst.transform:
            x_res = abs(src_dst.transform.a)
            y_res = abs(src_dst.transform.e)

        # Skip if we couldn't determine resolution
        if x_res is None or y_res is None:
            continue

        band_resolutions[band_name] = (x_res, y_res, asset_crs)

    return band_resolutions


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
    """Calculate output dimensions maintaining aspect ratio when only one dimension is provided."""

    # If both width and height are provided, return them directly
    if width and height:
        return width, height

    # Calculate native dimensions from resolution
    if x_resolution and y_resolution:
        native_width = int(round((bbox[2] - bbox[0]) / x_resolution))
        native_height = int(round((bbox[3] - bbox[1]) / y_resolution))
        aspect_ratio = native_width / native_height

        # Only width provided - calculate height to maintain proportions
        if width and not height:
            height = int(round(width / aspect_ratio))
            return width, height

        # Only height provided - calculate width to maintain proportions
        if height and not width:
            width = int(round(height * aspect_ratio))
            return width, height

        # Neither provided - use native dimensions
        return native_width, native_height

    # No resolution info - use default dimensions
    if not width and not height:
        return 1024, 1024

    # If we get here, we have resolution issues but one dimension was provided
    # Use provided dimension and default the other to 1024
    if width:
        return width, 1024
    if height:
        return 1024, height

    return 1024, 1024


def _check_pixel_limit(
    width: Optional[int],
    height: Optional[int],
    items_count: int,
    bands_count: int,
) -> None:
    """Check if pixel count exceeds maximum allowed.

    For mosaics, items with the same datetime are counted only once since they
    will be combined into a single mosaic.
    """
    from .settings import ProcessingSettings

    processing_settings = ProcessingSettings()

    width_int = int(width or 0)
    height_int = int(height or 0)

    pixel_count = width_int * height_int * items_count * bands_count
    if pixel_count > processing_settings.max_pixels:
        raise OutputLimitExceeded(
            width_int,
            height_int,
            processing_settings.max_pixels,
            items_count=items_count,
            bands_count=bands_count,
        )


def _estimate_output_dimensions(
    items: List[Dict],
    spatial_extent: Optional[SpatialExtent],
    bands: Optional[list[str]],
    width: Optional[int] = None,
    height: Optional[int] = None,
    check_max_pixels: bool = True,
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
    # Initialize target CRS and bbox
    target_crs = rasterio.crs.CRS.from_user_input(
        spatial_extent.crs if spatial_extent else "epsg:4326"
    )

    target_bbox = (
        [
            spatial_extent.west,
            spatial_extent.south,
            spatial_extent.east,
            spatial_extent.north,
        ]
        if spatial_extent
        else None
    )

    # Dictionary to store (resolution_x, resolution_y) lists per band and per time (cube)
    cube_resolutions: Dict[str, Dict[str, List[tuple[float, float, List[float]]]]] = {}

    # Process each item
    for item in items:
        with SimpleSTACReader(item) as src_dst:
            # Update bbox with ther intersection of current item bbox
            item_bbox = src_dst.bounds
            if item_bbox:
                item_polygon = box(
                    item_bbox[0], item_bbox[1], item_bbox[2], item_bbox[3]
                )
                if spatial_extent is None:
                    if target_bbox is None:
                        target_bbox = list(item_bbox)
                    else:
                        # Convert current bbox to polygon and union with item polygon
                        current_polygon = box(
                            target_bbox[0], target_bbox[1], target_bbox[2], target_bbox[3]
                        )
                        union = current_polygon.union(item_polygon)
                        if not union.is_empty:
                            target_bbox = list(union.bounds)

            # Get resolutions and CRS for this item's assets
            asset_resolutions = _get_assets_resolutions(item, src_dst, bands)
            for band_name, (x_res, y_res, asset_crs) in asset_resolutions.items():
                # Skip if we couldn't determine resolution
                if x_res is None or y_res is None:
                    continue

                # Reproject resolution if needed
                if asset_crs != target_crs:
                    x_res, y_res = _reproject_resolution(
                        asset_crs, target_crs, target_bbox, x_res, y_res
                    )
                    if x_res is None or y_res is None:
                        continue

                # Get item datetime for cube resolution grouping
                item_datetime = src_dst.item.datetime.isoformat()
                if item_datetime not in cube_resolutions:
                    cube_resolutions[item_datetime] = {}

                if band_name not in cube_resolutions[item_datetime]:
                    cube_resolutions[item_datetime][band_name] = []
                cube_resolutions[item_datetime][band_name].append(
                    (x_res, y_res, target_bbox)
                )

    # Find the minimum resolution across all bands
    x_resolution = None
    y_resolution = None
    for item in cube_resolutions.values():
        for resolutions in item.values():
            for x_res, y_res, _ in resolutions:
                if x_resolution is None or x_res < x_resolution:
                    x_resolution = x_res
                if y_resolution is None or y_res < y_resolution:
                    y_resolution = y_res

    if target_bbox is None:
        raise ValueError("No valid bounding box found in items")

    # Calculate dimensions maintaining aspect ratio
    width, height = _calculate_dimensions(
        target_bbox, x_resolution, y_resolution, width, height
    )

    # Check pixel limit
    if check_max_pixels:
        if width is None or height is None:
            raise ValueError("Width and height must be specified or calculated")
        _check_pixel_limit(width, height, len(cube_resolutions), len(max(cube_resolutions.values(), key=len)))

    return Dims(
        width=width,  # type: ignore
        height=height,  # type: ignore
        crs=target_crs,
        bbox=target_bbox,
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
