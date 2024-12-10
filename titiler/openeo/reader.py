"""titiler-openeo custom reader."""

import warnings
from typing import Any, Dict, Optional, Sequence, Tuple, Type, Union
from urllib.parse import urlparse

import attr
import rasterio
from morecantile import TileMatrixSet
from rasterio.transform import array_bounds
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
from rio_tiler.types import AssetInfo, Indexes
from rio_tiler.utils import cast_to_sequence


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
            crs_string = proj.get("code") or proj.get("epsg") or proj.get("wkt")
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
                self.crs = rasterio.crs.CRS.from_string(crs_string)

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
