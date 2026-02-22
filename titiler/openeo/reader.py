"""titiler-openeo custom reader."""

import logging
import time
import warnings
from typing import Any, Dict, List, Optional, Sequence, Tuple, Type, Union
from urllib.parse import urlparse

import attr
import pystac
import rasterio
from affine import Affine
from morecantile import TileMatrixSet
from openeo_pg_parser_networkx.pg_schema import BoundingBox
from pystac.extensions.projection import ProjectionExtension
from rasterio.errors import RasterioIOError
from rasterio.features import rasterize
from rasterio.transform import array_bounds
from rasterio.warp import transform_bounds, transform_geom
from rio_tiler.constants import WEB_MERCATOR_TMS, WGS84_CRS
from rio_tiler.errors import AssetAsBandError, InvalidAssetName, MissingAssets
from rio_tiler.io import Reader
from rio_tiler.io.base import BaseReader, MultiBaseReader
from rio_tiler.io.stac import STAC_ALTERNATE_KEY
from rio_tiler.models import ImageData
from rio_tiler.tasks import multi_arrays
from rio_tiler.types import AssetInfo, AssetType, BBox
from rio_tiler.utils import cast_to_sequence, inherit_rasterio_env
from typing_extensions import TypedDict

from .errors import OutputLimitExceeded

logger = logging.getLogger(__name__)


class Dims(TypedDict):
    """Estimate Dimensions."""

    width: int
    height: int
    bounds_crs: rasterio.crs.CRS
    crs: rasterio.crs.CRS
    bbox: List[float]


@attr.s
class SimpleSTACReader(MultiBaseReader):
    """Simplified STAC Reader."""

    tms: TileMatrixSet = attr.ib(default=WEB_MERCATOR_TMS)
    minzoom: int = attr.ib(default=None)
    maxzoom: int = attr.ib(default=None)

    assets: Sequence[str] = attr.ib(init=False)
    default_assets: Optional[Sequence[AssetType]] = attr.ib(default=None)

    reader: Type[BaseReader] = attr.ib(default=Reader)
    reader_options: Dict = attr.ib(factory=dict)

    ctx: Any = attr.ib(default=rasterio.Env)

    item: pystac.Item = attr.ib(init=False)

    def __attrs_post_init__(self) -> None:
        """Set reader spatial infos and list of valid assets."""
        self.item = self.input

        # Get bounding box and default CRS
        self.bounds = self.item.bbox
        self.crs = WGS84_CRS  # Default to WGS84

        # Get projection information using STAC extension
        if ProjectionExtension.has_extension(self.item):
            proj_ext = ProjectionExtension.ext(self.item)
            if all(
                [
                    proj_ext.transform,
                    proj_ext.shape,
                    proj_ext.crs_string,
                ]
            ):
                self.height, self.width = proj_ext.shape
                self.transform = Affine(*proj_ext.transform)
                self.bounds = array_bounds(self.height, self.width, self.transform)
                self.crs = rasterio.crs.CRS.from_string(proj_ext.crs_string)

        self.minzoom = self.minzoom if self.minzoom is not None else self._minzoom
        self.maxzoom = self.maxzoom if self.maxzoom is not None else self._maxzoom

        self.assets = self.item.get_assets().keys()

        if not self.assets:
            raise MissingAssets(
                "No valid asset found. Asset's media types not supported"
            )

    def _get_reader(self, asset_info: AssetInfo) -> type[BaseReader]:
        """Get Asset Reader."""
        return self.reader

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

    def _get_asset_info(self, asset: AssetType) -> AssetInfo:  # noqa: C901
        """Validate asset names and return asset's info.

        Args:
            asset (AssetType): STAC asset name.

        Returns:
            AssetInfo: STAC asset info.

        """
        asset_name: str
        if isinstance(asset, dict):
            if not asset.get("name"):
                raise ValueError("asset dictionary does not have `name` key")
            asset_name = asset["name"]
        else:
            asset_name = asset

        asset_name, vrt_options = self._parse_vrt_asset(asset_name)

        if asset_name not in self.assets:
            raise InvalidAssetName(
                f"'{asset}' is not valid, should be one of {self.assets}"
            )

        asset_info = self.item.assets[asset_name]
        extras = asset_info.extra_fields

        method_options: dict[str, Any] = {}
        reader_options: dict[str, Any] = {}
        if isinstance(asset, dict):
            if indexes := asset.get("indexes"):
                method_options["indexes"] = indexes
            if expr := asset.get("expression"):
                method_options["expression"] = expr

        asset_modified = "expression" in method_options or vrt_options

        info = {
            "url": asset_info.get_absolute_href() or asset_info.href,
            "name": asset_name,
            "media_type": asset_info.media_type,
            "reader_options": reader_options,
            "method_options": method_options,
        }

        if not asset_modified:
            info["metadata"] = extras

        if STAC_ALTERNATE_KEY and extras.get("alternate"):
            if alternate := extras["alternate"].get(STAC_ALTERNATE_KEY):
                info["url"] = alternate["href"]

        # https://github.com/stac-extensions/file
        if head := extras.get("file:header_size"):
            info["env"] = {"GDAL_INGESTED_BYTES_AT_OPEN": head}

        # https://github.com/stac-extensions/raster
        if (bands := extras.get("raster:bands", [])) and not asset_modified:
            stats = [
                (b["statistics"]["minimum"], b["statistics"]["maximum"])
                for b in bands
                if {"minimum", "maximum"}.issubset(b.get("statistics", {}))
            ]
            # check that stats data are all double and make warning if not
            if (
                stats
                and all(isinstance(v, (int, float)) for stat in stats for v in stat)
                and len(stats) == len(bands)
            ):
                info["dataset_statistics"] = stats
            else:
                logger.warning(
                    "Some statistics data in STAC are invalid, they will be ignored."
                )

            # Extract nodata from raster:bands if present.
            # This is critical for proper mosaicking: pixels with nodata values
            # will be masked, allowing subsequent tiles to fill those areas.
            #
            # Look for nodata in multiple possible locations:
            # - nodata (per STAC raster extension v2.0)
            # - raster:nodata (deprecated but still common in older catalogs)
            nodata_values = []
            for b in bands:
                nodata = b.get("nodata") or b.get("raster:nodata")
                if nodata is not None:
                    nodata_values.append(nodata)

            # Only use nodata if all bands have the same value
            if len(set(nodata_values)) == 1:
                info["reader_options"]["nodata"] = nodata_values[0]

        # Extract nodata from asset level if not found in raster:bands.
        # Asset-level nodata is common in STAC catalogs like Copernicus Sentinel-2
        # where each asset (e.g., B04.tif) has a "nodata": 0 field.
        if "nodata" not in info["reader_options"] and not vrt_options:
            asset_nodata = extras.get("nodata")
            if asset_nodata is not None:
                info["reader_options"]["nodata"] = asset_nodata

        if vrt_options:
            info["url"] = f"vrt://{info['url']}?{vrt_options}"

        return info

    # The regular STAC Reader doesn't have a `read` method
    def read(
        self,
        assets: Optional[Union[Sequence[AssetType], AssetType]] = None,
        expression: Optional[str] = None,
        asset_as_band: bool = False,
        **kwargs: Any,
    ) -> ImageData:
        """Read and merge previews from multiple assets.

        Args:
            assets (sequence of str or str, optional): assets to fetch info from.
            expression (str, optional): rio-tiler expression (e.g. b1/b2+b3).
            asset_as_band (bool, optional): treat each asset as a separate band. Defaults to False.
            kwargs (optional): Options to forward to the `self.reader.preview` method.

        Returns:
            rio_tiler.models.ImageData: ImageData instance with data, mask and tile spatial info.

        """
        if kwargs.pop("asset_indexes", None):
            warnings.warn(
                "`asset_indexes` parameter is deprecated in `tile` method and will be ignored.",
                DeprecationWarning,
                stacklevel=2,
            )

        assets = cast_to_sequence(assets)
        if not assets and self.default_assets:
            logger.warning(
                "No assets/expression passed, defaults to %s", self.default_assets
            )
            assets = self.default_assets

        if not assets:
            raise MissingAssets(
                "No Asset defined by `assets` option or class-level `default_assets`."
            )

        @inherit_rasterio_env
        def _reader(asset: AssetType, **kwargs: Any) -> ImageData:
            asset_info = self._get_asset_info(asset)
            asset_name = asset_info["name"]
            reader = self._get_reader(asset_info)
            reader_options = {**self.reader_options, **asset_info["reader_options"]}
            method_options = {**asset_info["method_options"], **kwargs}

            with self.ctx(**asset_info.get("env", {})):
                with reader(asset_info["url"], tms=self.tms, **reader_options) as src:
                    data = src.preview(**method_options)

                    self._update_statistics(
                        data,
                        statistics=asset_info.get("dataset_statistics"),
                    )

                    metadata = data.metadata or {}
                    if m := asset_info.get("metadata"):
                        metadata.update(m)
                    data.metadata = {asset: metadata}

                    data.band_descriptions = [
                        f"{asset_name}_{n}" for n in data.band_descriptions
                    ]
                    if asset_as_band:
                        if len(data.band_names) > 1:
                            raise AssetAsBandError(
                                "Can't use `asset_as_band` for multibands asset"
                            )
                        data.band_descriptions = [asset_name]

                    return data

        img = multi_arrays(assets, _reader, **kwargs)
        img.band_names = [f"b{ix + 1}" for ix in range(img.count)]
        if expression:
            return img.apply_expression(expression)

        return img


def _get_asset_crs(
    item: pystac.Item,
    asset: pystac.Asset,
    asset_proj_ext: Optional[ProjectionExtension],
) -> Optional[rasterio.crs.CRS]:
    """Get CRS from asset using various metadata sources.

    Args:
        item: STAC item
        asset: STAC asset
        asset_proj_ext: Asset's projection extension

    Returns:
        CRS object or None if not found
    """
    if asset_proj_ext:
        if asset_proj_ext.epsg:
            return rasterio.crs.CRS.from_epsg(asset_proj_ext.epsg)
        if asset_proj_ext.wkt2:
            return rasterio.crs.CRS.from_wkt(asset_proj_ext.wkt2)
        if asset_proj_ext.crs_string:
            return rasterio.crs.CRS.from_string(asset_proj_ext.crs_string)

    if proj_code := asset.extra_fields.get("proj:code"):
        return rasterio.crs.CRS.from_string(proj_code)

    return None


def _get_asset_resolution(
    item: pystac.Item,
    asset: pystac.Asset,
    asset_proj_ext: Optional[ProjectionExtension],
    src_dst: SimpleSTACReader,
) -> Tuple[Optional[float], Optional[float]]:
    """Get x and y resolutions from asset metadata.

    Args:
        item: STAC item
        asset: STAC asset
        asset_proj_ext: Asset's projection extension
        src_dst: SimpleSTACReader instance

    Returns:
        Tuple of (x_resolution, y_resolution), either may be None
    """
    if asset_proj_ext and asset_proj_ext.transform:
        return (abs(asset_proj_ext.transform[0]), abs(asset_proj_ext.transform[4]))

    if asset_proj_ext and asset_proj_ext.shape:
        bbox = item.bbox
        shape = asset_proj_ext.shape
        if shape[0] > 0 and shape[1] > 0:
            return (
                abs((bbox[2] - bbox[0]) / shape[0]),
                abs((bbox[3] - bbox[1]) / shape[1]),
            )

    if src_dst.transform:
        return abs(src_dst.transform.a), abs(src_dst.transform.e)

    return None, None


def _get_assets_resolutions(
    item: pystac.Item,
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
    assets_to_process = set(bands) if bands else set(item.get_assets().keys())

    for band_name in assets_to_process:
        if band_name not in item.assets:
            continue

        asset = item.assets[band_name]
        asset_proj_ext = None
        if ProjectionExtension.has_extension(item):
            asset_proj_ext = ProjectionExtension.ext(asset)

        # Get asset CRS or fall back to item CRS
        asset_crs = _get_asset_crs(item, asset, asset_proj_ext) or src_dst.crs

        # Get asset resolution
        x_res, y_res = _get_asset_resolution(item, asset, asset_proj_ext, src_dst)

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


def _get_native_crs_from_item(
    item: pystac.Item,
    reader_crs: Optional[rasterio.crs.CRS],
) -> Optional[rasterio.crs.CRS]:
    """Extract native CRS from a STAC item.

    Tries multiple sources in order:
    1. Reader CRS (if non-WGS84, from full projection info)
    2. Item-level proj:epsg via ProjectionExtension
    3. First asset's proj:epsg via ProjectionExtension

    Args:
        item: STAC item
        reader_crs: CRS from SimpleSTACReader (may be WGS84 if no full proj info)

    Returns:
        Native CRS or None if not found
    """
    # First check if reader has non-WGS84 CRS (from full projection info)
    if reader_crs and reader_crs != WGS84_CRS:
        return reader_crs

    # Then check item-level proj:epsg via ProjectionExtension
    if ProjectionExtension.has_extension(item):
        proj_ext = ProjectionExtension.ext(item)
        if proj_ext.epsg:
            return rasterio.crs.CRS.from_epsg(proj_ext.epsg)
        if proj_ext.crs_string:
            return rasterio.crs.CRS.from_string(proj_ext.crs_string)

    # Finally check first asset's proj:epsg via ProjectionExtension
    if ProjectionExtension.has_extension(item):
        for asset in item.assets.values():
            asset_proj = ProjectionExtension.ext(asset)
            if asset_proj.epsg:
                return rasterio.crs.CRS.from_epsg(asset_proj.epsg)
            if asset_proj.crs_string:
                return rasterio.crs.CRS.from_string(asset_proj.crs_string)

    return None


def _get_target_crs_bbox(
    items: List[pystac.Item],
    spatial_extent: Optional[BoundingBox],
    target_crs: Optional[Union[int, str, rasterio.crs.CRS]] = None,
) -> Tuple[rasterio.crs.CRS, rasterio.crs.CRS, List[float]]:
    """Get bounds CRS, target CRS, and bbox from items and spatial extent.

    Args:
        items: List of STAC items
        spatial_extent: Optional bounding box for the output
        target_crs: Optional target CRS for the output. If None, uses native CRS from first item.

    Returns:
        Tuple of (bounds_crs, target_crs, bbox) where:
            - bounds_crs: CRS of the input bounding box coordinates
            - target_crs: CRS for the output data
            - bbox: Bounding box as [west, south, east, north]
    """
    # bounds_crs is always from spatial_extent or WGS84
    bounds_crs = (
        rasterio.crs.CRS.from_user_input(spatial_extent.crs)
        if spatial_extent and spatial_extent.crs
        else WGS84_CRS
    )

    target_bbox: List[float] = (
        [
            spatial_extent.west,
            spatial_extent.south,
            spatial_extent.east,
            spatial_extent.north,
        ]
        if spatial_extent
        else []
    )

    # Determine the native CRS from items (for when target_crs is None)
    native_crs: Optional[rasterio.crs.CRS] = None

    # Process each item to update bbox and find native CRS
    for item in items:
        with SimpleSTACReader(item) as src_dst:
            item_bbox = src_dst.bounds
            if item_bbox:
                if not spatial_extent:
                    if not target_bbox:
                        target_bbox = list(item_bbox)
                    else:
                        # Compute union of two bounding boxes
                        target_bbox = [
                            min(target_bbox[0], item_bbox[0]),  # west
                            min(target_bbox[1], item_bbox[1]),  # south
                            max(target_bbox[2], item_bbox[2]),  # east
                            max(target_bbox[3], item_bbox[3]),  # north
                        ]

            # Capture native CRS from first item
            if native_crs is None:
                native_crs = _get_native_crs_from_item(item, src_dst.crs)

    if not target_bbox:
        raise ValueError("No valid bounding box found in items")

    # Determine output CRS
    if target_crs is not None:
        # User explicitly specified target CRS
        if isinstance(target_crs, rasterio.crs.CRS):
            output_crs = target_crs
        elif isinstance(target_crs, int):
            output_crs = rasterio.crs.CRS.from_epsg(target_crs)
        else:
            output_crs = rasterio.crs.CRS.from_user_input(target_crs)
    elif native_crs is not None:
        # Use native CRS from items
        output_crs = native_crs
    else:
        # Fallback to bounds CRS
        output_crs = bounds_crs

    return bounds_crs, output_crs, target_bbox


def _get_cube_resolutions(
    items: List[Dict],
    target_crs: rasterio.crs.CRS,
    target_bbox: List[float],
    bands: Optional[list[str]],
) -> Dict[str, Dict[str, List[Tuple[float, float, List[float]]]]]:
    """Get resolutions for each datetime and band combination."""
    cube_resolutions: Dict[str, Dict[str, List[Tuple[float, float, List[float]]]]] = {}

    for item in items:
        with SimpleSTACReader(item) as src_dst:
            asset_resolutions = _get_assets_resolutions(item, src_dst, bands)
            for band_name, (x_res, y_res, asset_crs) in asset_resolutions.items():
                if x_res is None or y_res is None:
                    continue

                x_val: float = float(x_res)
                y_val: float = float(y_res)

                if asset_crs != target_crs:
                    reprojected = _reproject_resolution(
                        asset_crs,
                        target_crs,
                        target_bbox,
                        x_val,
                        y_val,
                    )
                    if reprojected[0] is None or reprojected[1] is None:
                        continue
                    x_val = float(reprojected[0])
                    y_val = float(reprojected[1])

                item_datetime = src_dst.item.datetime.isoformat()
                if item_datetime not in cube_resolutions:
                    cube_resolutions[item_datetime] = {}

                if band_name not in cube_resolutions[item_datetime]:
                    cube_resolutions[item_datetime][band_name] = []

                cube_resolutions[item_datetime][band_name].append(
                    (x_val, y_val, target_bbox)
                )

    return cube_resolutions


def _estimate_output_dimensions(
    items: List[pystac.Item],
    spatial_extent: Optional[BoundingBox],
    bands: Optional[list[str]],
    width: Optional[int] = None,
    height: Optional[int] = None,
    check_max_pixels: bool = True,
    target_crs: Optional[Union[int, str, rasterio.crs.CRS]] = None,
) -> Dims:
    """
    Estimate output dimensions based on items and spatial extent.

    Args:
        items: List of STAC items
        spatial_extent: Bounding box for the output
        bands: List of band names to include
        width: Optional user-specified width
        height: Optional user-specified height
        check_max_pixels: Whether to check pixel count limit
        target_crs: Optional target CRS for the output. If None, uses native CRS from first item.

    Returns:
        Dictionary containing:
            - width: Estimated or specified width
            - height: Estimated or specified height
            - bounds_crs: CRS of the input bounding box
            - crs: Target CRS to use for output
            - bbox: Bounding box as a list [west, south, east, north]
    """
    # Get bounds CRS, target CRS, and bbox
    bounds_crs, output_crs, target_bbox = _get_target_crs_bbox(
        items, spatial_extent, target_crs
    )

    # Get resolutions for each datetime and band
    cube_resolutions = _get_cube_resolutions(items, output_crs, target_bbox, bands)

    # Find the minimum resolution across all bands
    x_resolution: Optional[float] = None
    y_resolution: Optional[float] = None
    for item in cube_resolutions.values():
        for resolutions in item.values():
            for x_res, y_res, _ in resolutions:
                if x_resolution is None or x_res < x_resolution:
                    x_resolution = x_res
                if y_resolution is None or y_res < y_resolution:
                    y_resolution = y_res

    # Calculate dimensions maintaining aspect ratio
    width, height = _calculate_dimensions(
        target_bbox, x_resolution, y_resolution, width, height
    )

    # Check pixel limit if requested
    if check_max_pixels:
        if width is None or height is None:
            raise ValueError("Width and height must be specified or calculated")
        _check_pixel_limit(
            width,
            height,
            len(cube_resolutions),
            len(max(cube_resolutions.values(), key=len)) if cube_resolutions else 0,
        )

    return Dims(
        width=width,  # type: ignore
        height=height,  # type: ignore
        bounds_crs=bounds_crs,
        crs=output_crs,
        bbox=target_bbox,
    )


def _reader(item: Dict[str, Any], bbox: BBox, **kwargs: Any) -> ImageData:
    """
    Read a STAC item and return an ImageData object.

    Args:
        item: STAC item dictionary (converted to pystac.Item by SimpleSTACReader)
        bbox: Bounding box to read
        **kwargs: Additional keyword arguments to pass to the reader

    Returns:
        ImageData object with cutline_mask set from item geometry if available
    """
    max_retries = 10
    retry_delay = 1.0  # seconds
    retries = 0

    # Extract item info for logging
    item_id = (
        item.get("id", "unknown")
        if isinstance(item, dict)
        else getattr(item, "id", "unknown")
    )
    item_datetime = (
        item.get("properties", {}).get("datetime", "unknown")
        if isinstance(item, dict)
        else getattr(item, "datetime", None) or "unknown"
    )

    logger.debug(f"Loading STAC item: {item_id} (datetime: {item_datetime})")

    while True:
        try:
            with SimpleSTACReader(item) as src_dst:
                img = src_dst.part(bbox, **kwargs)

                # IMPORTANT: We intentionally do NOT set cutline_mask on individual tiles.
                #
                # Background: rio-tiler's mosaic_reader uses cutline_mask from the FIRST
                # image to determine when mosaicking is complete (via FirstMethod.is_done).
                # The is_done check only verifies that pixels INSIDE the first tile's
                # footprint geometry are filled, ignoring pixels outside that footprint.
                #
                # Problem: For multi-tile mosaics where each tile covers only a portion
                # of the target bbox, this causes early termination after the first tile.
                # Example: If tile 1 covers 7% of the bbox and has valid data for that 7%,
                # is_done returns True even though 93% of the mosaic is still empty.
                #
                # Solution: By not setting cutline_mask, is_done falls back to checking
                # if ALL pixels in the mosaic are filled (not numpy.ma.is_masked(mosaic)).
                # This allows mosaicking to continue until all tiles are processed or
                # all pixels have valid data.
                #
                # The nodata mask (created from the nodata value in STAC metadata)
                # correctly tracks which pixels have valid data vs nodata, and this
                # mask is properly combined during mosaicking via FirstMethod.feed().

                logger.debug(
                    f"  Loaded {item_id}: {img.width}x{img.height}, "
                    f"bands={img.count}, dtype={img.data.dtype}"
                )

                return img
        except RasterioIOError as e:
            retries += 1
            if retries >= max_retries:
                # If we've reached max retries, re-raise the exception
                logger.error(
                    f"Failed to load {item_id} after {max_retries} retries: {e}"
                )
                raise
            # Log the error and retry after a delay
            logger.warning(
                f"RasterioIOError loading {item_id}: {str(e)}. "
                f"Retrying in {retry_delay}s... (Attempt {retries}/{max_retries})"
            )
            time.sleep(retry_delay)
            # Increase delay for next retry (exponential backoff)
            retry_delay *= 2


def _apply_cutline_mask(
    img: ImageData,
    geometry: Dict[str, Any],
    dst_crs: Optional[rasterio.crs.CRS] = None,
) -> ImageData:
    """Apply a cutline mask to an ImageData object based on item geometry.

    Creates a mask from a geometry (e.g., STAC item footprint) indicating which
    pixels fall inside vs outside the geometry.

    IMPORTANT: This function should NOT be used on individual tiles when mosaicking
    multiple STAC items. mosaic_reader uses cutline_mask from the FIRST image only
    for early termination, which causes incorrect behavior when tiles partially
    overlap the target bbox. See the documentation in _reader() for details.

    Use cases where cutline_mask IS appropriate:
    - Single-tile reads (no mosaicking)
    - Post-mosaic masking with aggregated geometry
    - Clipping to a user-provided geometry

    Args:
        img: ImageData object to apply the mask to
        geometry: GeoJSON geometry dict (typically in EPSG:4326)
        dst_crs: Target CRS for the geometry transformation

    Returns:
        ImageData object with cutline_mask set (True = outside geometry)
    """
    # Transform geometry from WGS84 to the destination CRS if needed
    if dst_crs is not None and dst_crs != WGS84_CRS:
        geometry = transform_geom(WGS84_CRS, dst_crs, geometry)

    # Create cutline mask using rasterize
    # The mask is True where pixels are OUTSIDE the geometry (invalid)
    cutline_mask = rasterize(
        [geometry],
        out_shape=(img.height, img.width),
        transform=img.transform,
        default_value=0,
        fill=1,
        dtype="uint8",
    ).astype("bool")

    img.cutline_mask = cutline_mask
    return img
