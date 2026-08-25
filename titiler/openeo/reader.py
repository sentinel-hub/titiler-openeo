"""titiler-openeo custom reader."""

import logging
import time
import warnings
from threading import Lock
from typing import Any, Dict, List, Optional, Sequence, Tuple, Type, Union, cast

import attr
import numpy
import pystac
import rasterio
from morecantile import TileMatrixSet
from openeo_pg_parser_networkx.pg_schema import BoundingBox
from pystac.extensions.projection import ProjectionExtension
from rasterio.errors import RasterioIOError
from rasterio.features import bounds as featureBounds
from rasterio.features import rasterize
from rasterio.vrt import WarpedVRT
from rasterio.warp import transform_bounds, transform_geom
from rio_tiler.constants import WEB_MERCATOR_TMS, WGS84_CRS
from rio_tiler.errors import AssetAsBandError, InvalidAssetName, MissingAssets
from rio_tiler.io import Reader
from rio_tiler.io.base import BaseReader, MultiBaseReader
from rio_tiler.io.stac import STAC_ALTERNATE_KEY, _extract_proj_info
from rio_tiler.models import ImageData
from rio_tiler.tasks import multi_arrays
from rio_tiler.types import AssetInfo, AssetType, AssetWithOptions, BBox
from rio_tiler.utils import cast_to_sequence, has_alpha_band, inherit_rasterio_env
from typing_extensions import TypedDict

from .bandsources import (
    BAND_SOURCES,
    ResolvedBand,
    SiblingCandidateFacts,
    derive_bands,
    resolve_band,
)
from .errors import OutputLimitExceeded
from .settings import ProcessingSettings
from .signing import HrefSigner, signer_for_item

logger = logging.getLogger(__name__)

processing_settings = ProcessingSettings()


class Dims(TypedDict):
    """Estimate Dimensions."""

    width: int
    height: int
    bounds_crs: rasterio.crs.CRS
    crs: rasterio.crs.CRS
    bbox: List[float]


@attr.s
class OpenEOReader(Reader):
    """titiler-openeo's asset reader: rio-tiler's ``Reader`` plus our own fixes.

    This is the single customisation point for how titiler-openeo reads a
    raster asset. It inherits everything from ``rio_tiler.io.Reader`` -- driver
    and format support (COG, Zarr, xarray, ...), overviews, decimation, CRS
    handling, resampling, masks, output-size limits -- and overrides only the
    specific behaviours we need to change. New per-asset read behaviour belongs
    here rather than in a parallel reader; see
    docs/adr/0001-sar-backscatter.md S7.10, which anticipates this class
    growing to carry reader requirements resolved from the process graph.

    Current overrides:

    **Georeferencing of GCP datasets.** rio-tiler detects GCPs but collapses
    them to a single affine via ``transform.from_gcps()`` and passes it as
    ``src_transform``. Because a ``src_transform`` is supplied, GDAL never sees
    the GCPs and cannot do its own higher-order fit. For grids that are not
    well approximated by an affine -- Sentinel-1 GRD, where slant-to-ground
    range distortion and meridian convergence make the grid strongly
    non-affine -- that matters, and it matters more the further from the
    equator you go. Measured divergence between the two warp paths on real
    products: <= 30 m at 69 deg N, but 204-2042 m at 81-86 deg N.

    Omitting ``src_transform`` and capping at ``MAX_GCP_ORDER=3`` lets GDAL
    warp from the real GCPs. Order 3 is both sufficient and GDAL's ceiling:
    ``MAX_GCP_ORDER=2`` produces a bit-identical VRT to the affine (GDAL falls
    back to order 1) and ``4`` raises ``Failed to compute GCP transform``.

    This cannot be done from the outside: the collapse happens at dataset-open
    time, and by the time ``part()`` runs the dataset is a ``WarpedVRT``
    holding zero GCPs, so ``vrt_options`` arrives too late.

    Datasets without GCPs are untouched and fall through to ``Reader``
    unchanged -- the same condition rio-tiler itself gates on, so this adds no
    cost for non-GCP assets.

    See ADR 0001 S1.6i, issue #343, and the upstream discussion at
    https://github.com/cogeotiff/rio-tiler/issues/977 -- this mirrors the fix
    proposed there by rio-tiler's maintainer. When it ships upstream, drop the
    GCP override and keep the class as the customisation point.
    """

    def __attrs_post_init__(self):
        """Open the dataset, warping from real GCPs when the dataset has them."""
        if not self.dataset:
            self.dataset = self._ctx_stack.enter_context(rasterio.open(self.input))

        if self.dataset.gcps[0]:
            vrt_options: Dict[str, Any] = {
                "src_crs": self.dataset.gcps[1],
                "MAX_GCP_ORDER": 3,
                "add_alpha": True,
            }

            if self.dataset.nodata is not None:
                vrt_options.update(
                    {
                        "nodata": self.dataset.nodata,
                        "add_alpha": False,
                        "src_nodata": self.dataset.nodata,
                    }
                )

            if has_alpha_band(self.dataset):
                vrt_options.update({"add_alpha": False})

            self.dataset = self._ctx_stack.enter_context(
                WarpedVRT(self.dataset, **vrt_options)
            )

        # Delegate the remaining setup (bounds, crs, dtype, colormap, zooms) to
        # rio-tiler. Our VRT has already consumed the GCPs, so the parent's own
        # GCP branch is a no-op -- which keeps this override tied to the one
        # thing it changes, rather than duplicating a tail that varies by
        # rio-tiler version.
        super().__attrs_post_init__()


#: Extensions that are never the measurement raster itself (annotation XML,
#: STAC-API tilejson, manifests, ...). Used only to skip pointless header
#: opens in `_item_has_untrustworthy_proj` -- not a correctness filter.
_NON_RASTER_HREF_SUFFIXES = (".xml", ".json", ".zip", ".txt", ".html")


def _item_looks_like_sar(item: pystac.Item) -> bool:
    """Cheap, no-I/O signal that ``item`` may carry GCP-referenced SAR-geometry assets.

    ``sar:instrument_mode`` (SAR STAC extension) is present on Sentinel-1 GRD items
    from every catalogue this project has checked -- CDSE, Earth Search, Planetary
    Computer (docs/adr/0001-sar-backscatter.md S1.7) -- including the two that
    fabricate a bbox-derived `proj:transform` for them (issue #338). This only gates
    whether `_item_has_untrustworthy_proj` pays for a header open below: terrain
    corrected/geocoded SAR products (e.g. RTC) can carry this same field and still
    have genuinely valid `proj:*`, so presence alone is not treated as proof.
    """
    return "sar:instrument_mode" in (item.properties or {})


def _is_asset_gcp_referenced(href: str) -> bool:
    """Open ``href`` header-only and report whether it is GCP-referenced with no CRS.

    A dataset in this state (Sentinel-1 GRD's SAR geometry is the known case) has no
    valid affine transform, so any `proj:epsg`/`proj:transform` a catalogue advertises
    for it cannot be correct. This is a plain ``rasterio.open`` -- metadata only, no
    pixel data -- mirroring the header-only GCP read `OpenEOReader` and
    `sar/geocode.get_gcps` already rely on elsewhere in this codebase.
    """
    try:
        with rasterio.open(href) as dataset:
            return dataset.crs is None and bool(dataset.gcps[0])
    except Exception:
        logger.debug(
            "Could not open asset %r to check GCP georeferencing", href, exc_info=True
        )
        return False


def _item_has_untrustworthy_proj(
    item: pystac.Item,
    assets: Sequence[str],
    signer: Optional[HrefSigner] = None,
) -> bool:
    """Whether ``item``'s `proj:*` metadata should be ignored (issue #338).

    Gated by `_item_looks_like_sar` so ordinary (non-SAR) items never pay for the
    header open below -- this keeps the common case exactly as cheap as before.
    """
    if not _item_looks_like_sar(item):
        return False

    for asset_name in assets:
        asset = item.assets.get(asset_name)
        if asset is None:
            continue

        # Deliberately the same helper the pixels are read through, not a raw
        # `get_absolute_href()`: this opens the asset for real, so it must see
        # the identical href variant `_get_asset_info` will resolve moments
        # later -- both the `alternate` choice and any credential on it
        # (docs/adr/0005-asset-href-signing.md S1.1).
        href = _resolve_asset_href(asset, signer)
        if not href or href.lower().endswith(_NON_RASTER_HREF_SUFFIXES):
            continue

        if _is_asset_gcp_referenced(href):
            return True

    return False


def _resolve_asset_href(
    asset: pystac.Asset,
    signer: Optional[HrefSigner] = None,
) -> str:
    """An asset's href, preferring the ``STAC_ALTERNATE_KEY`` variant if present.

    Shared by real and derived (band-source) assets so both resolve the same
    way for the same underlying file -- e.g. a Sentinel-1 measurement asset's
    GCPs (read for a derived band's sibling) must come from the same href
    variant its pixels are read from.

    ``signer`` applies last, after the alternate has been chosen, so a
    credential is attached to the href actually being opened. ``None`` -- the
    default everywhere -- returns exactly the string this function returned
    before signing existed (docs/adr/0005-asset-href-signing.md S2.2).
    """
    href = asset.get_absolute_href() or asset.href
    extras = asset.extra_fields
    if STAC_ALTERNATE_KEY and extras.get("alternate"):
        if alternate := extras["alternate"].get(STAC_ALTERNATE_KEY):
            href = alternate["href"]
    return signer(href) if signer is not None else href


@attr.s
class SimpleSTACReader(MultiBaseReader):
    """Simplified STAC Reader."""

    input: pystac.Item = attr.ib()

    tms: TileMatrixSet = attr.ib(default=WEB_MERCATOR_TMS)
    minzoom: int = attr.ib(default=None)
    maxzoom: int = attr.ib(default=None)

    assets: Sequence[str] = attr.ib(init=False)
    default_assets: Optional[Sequence[AssetType]] = attr.ib(default=None)

    reader: Type[BaseReader] = attr.ib(default=OpenEOReader)
    reader_options: Dict = attr.ib(factory=dict)

    ctx: Any = attr.ib(default=rasterio.Env)

    #: Optional AssetFetcher override for derived (band-source) assets' own
    #: non-raster fetches (e.g. Sentinel-1 annotation XML) -- a test/deployment
    #: seam mirroring `sar_backscatter`'s `options["fetcher"]`, one level down;
    #: not part of any user-facing contract. Real raster assets never see this
    #: -- only `_get_derived_asset_info` reads it, per derived asset, so it
    #: cannot leak into a real asset's `reader_options` (unlike `reader_options`
    #: above, which is shared by every asset this reader constructs).
    band_source_fetcher: Any = attr.ib(default=None)

    #: Optional href signer, resolved in `__attrs_post_init__` from the key the
    #: item was stamped with at ingest -- never passed in. Applied by
    #: `_resolve_asset_href` to every href this reader opens: real assets,
    #: band-source annotation assets and their sibling measurement hrefs alike,
    #: which is why one seam covers all three
    #: (docs/adr/0005-asset-href-signing.md S2.6).
    #:
    #: Resolving here rather than receiving it is what makes the credential
    #: fresh: `_reader` rebuilds this reader on every retry, so an expired token
    #: is re-minted rather than reused (ADR 0005 S3.1). `None` -- an unstamped
    #: item -- means "this deployment needs no credential on its hrefs".
    signer: Optional[HrefSigner] = attr.ib(init=False, default=None)

    #: Derived band names this item's own assets resolve to, precomputed once
    #: (pure, no I/O -- regex matching over `self.input.assets`) so
    #: `_get_asset_info` and the mask-inheritance post-step in `_reader()`
    #: don't repeat it. See docs/adr/0002-band-sources.md S2.3.
    _derived_bands: Dict[str, ResolvedBand] = attr.ib(init=False, factory=dict)

    #: Shared, per-instance memo for band-source readers' inverse maps
    #: (`BandReader.inverse_map_cache`). Every derived-band reader built for
    #: this item's `part()` calls gets *this same dict*: reads sharing one
    #: destination grid and one measurement asset's GCPs get an identical
    #: fit, so it is computed once however many bands from that asset are
    #: requested together (increment 3's calibration bands are the first
    #: case where one item can carry several). Dies with this instance --
    #: not a module-global cache, which would have no natural eviction.
    _inverse_map_cache: Dict[Any, Any] = attr.ib(init=False, factory=dict)

    #: Guards `_inverse_map_cache` (`BandReader.inverse_map_lock`) --
    #: `RasterStack` reads assets via a thread pool, so several derived-band
    #: readers for this item can build concurrently; without this, multiple
    #: threads can all miss the cache before any stores a result.
    _inverse_map_lock: Lock = attr.ib(init=False, factory=Lock)

    def __attrs_post_init__(self) -> None:
        """Set reader spatial infos and list of valid assets."""
        # Before anything that resolves an href: `_item_has_untrustworthy_proj`
        # below opens an asset for real, and must see the same credential the
        # pixels will be read with (ADR 0005 S1.1).
        self.signer = signer_for_item(self.input)

        self.assets = self.input.get_assets().keys()
        if not self.assets:
            raise MissingAssets(
                "No valid asset found. Asset's media types not supported"
            )

        item_asset_facts = [
            (key, asset.media_type, asset.roles or [])
            for key, asset in self.input.assets.items()
        ]
        # Richer than item_asset_facts (adds declared gsd) -- only consulted
        # by a BandSource whose sibling has no name expressible as a fixed
        # template (docs/adr/0004-sentinel2-view-sun-angle-bands.md S2.1).
        sibling_candidates = [
            (key, asset.media_type, asset.roles or [], asset.extra_fields.get("gsd"))
            for key, asset in self.input.assets.items()
        ]
        collection_id = self.input.collection_id or ""
        for name in derive_bands(collection_id, item_asset_facts, BAND_SOURCES):
            resolved = resolve_band(
                collection_id,
                name,
                item_asset_facts,
                BAND_SOURCES,
                sibling_candidates=sibling_candidates,
            )
            if resolved is not None:
                self._derived_bands[name] = resolved

        proj = _extract_proj_info(self.input, assets=self.assets)
        if proj and _item_has_untrustworthy_proj(self.input, self.assets, self.signer):
            logger.warning(
                "Ignoring STAC `proj:*` metadata for item '%s': its assets are "
                "GCP-referenced with no CRS (SAR geometry), so the catalogue's "
                "advertised projection is not a valid affine transform for this "
                "data. Falling back to the item's footprint bounding box.",
                self.input.id,
            )
            proj = None

        if proj:
            self.height = proj["height"]
            self.width = proj["width"]
            self.bounds = proj["bounds"]
            self.transform = proj["transform"]
            self.crs = proj["crs"]
        else:
            self.bounds = (
                tuple(self.input.bbox)
                if self.input.bbox
                else featureBounds(self.input.geometry)
            )
            self.crs = WGS84_CRS

        self.minzoom = self.minzoom if self.minzoom is not None else self._minzoom
        self.maxzoom = self.maxzoom if self.maxzoom is not None else self._maxzoom

    def _get_reader(self, asset_info: AssetInfo) -> type[BaseReader]:
        """Get Asset Reader."""
        resolved = self._derived_bands.get(asset_info["name"])
        if resolved is not None:
            return resolved.reader
        return self.reader

    def _get_options(
        self,
        asset: AssetWithOptions,
        metadata: pystac.Asset,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Copy from rio_tiler.io.stac._get_options."""
        method_options: dict[str, Any] = {}
        reader_options: dict[str, Any] = {}

        # Indexes
        if indexes := asset.get("indexes"):
            method_options["indexes"] = indexes
        # Expression
        if expr := asset.get("expression"):
            method_options["expression"] = expr
        # Bands
        if bands := asset.get("bands"):
            stac_bands = (
                metadata.extra_fields.get("bands")
                or metadata.extra_fields.get("eo:bands")  # V1.0
            )
            if not stac_bands:
                raise ValueError(
                    "Asset does not have 'bands' metadata, unable to use 'bands' option"
                )

            # There is no standard for precedence between 'eo:common_name' and 'name'
            # in STAC specification, so we will use 'eo:common_name' if it exists,
            # otherwise fallback to 'name', and if not exist use the band index as last resource.
            # The positional fallback key is stringified: `bands` values arrive as
            # strings (the openEO `bands` argument, and rio-tiler's asset options),
            # so an int key here can never be matched and the fallback was dead --
            # requesting band "1" raised "not found in asset metadata" instead of
            # selecting index 1.
            common_to_variable = {
                b.get("eo:common_name")
                or b.get("common_name")
                or b.get("name")
                or str(ix): ix
                for ix, b in enumerate(stac_bands, 1)
            }
            band_indexes: list[int] = []
            for b in bands:
                if idx := common_to_variable.get(b):
                    band_indexes.append(idx)
                else:
                    raise ValueError(
                        f"Band '{b}' not found in asset metadata, unable to use 'bands' option"
                    )

                method_options["indexes"] = band_indexes

        return reader_options, method_options

    def _get_derived_asset_info(self, asset_name: str) -> AssetInfo:
        """Build an ``AssetInfo`` for a band-source-derived (pseudo-asset) band.

        Resolves to the annotation asset that produces it (e.g. `vv_noise_lut`'s
        `schema-noise-vv`) and, if the band needs one, its sibling measurement
        asset -- for `NoiseBandReader`/`CalibrationBandReader`, the sibling's
        GCPs (docs/adr/0002-band-sources.md S2.3). Both hrefs go through
        `_resolve_asset_href` so a derived band and its sibling's own pixels
        resolve the same href variant for the same file.
        """
        resolved = self._derived_bands[asset_name]
        annotation_asset = self.input.assets[resolved.asset_key]

        reader_options: Dict[str, Any] = {
            "fetcher": self.band_source_fetcher,
            "quantity": resolved.quantity,
            "inverse_map_cache": self._inverse_map_cache,
            "inverse_map_lock": self._inverse_map_lock,
        }
        if resolved.sibling_key:
            sibling = self.input.assets.get(resolved.sibling_key)
            if sibling is None:
                raise InvalidAssetName(
                    f"Band '{asset_name}' needs sibling asset '{resolved.sibling_key}', "
                    f"which item '{self.input.id}' does not have"
                )
            reader_options["sibling_href"] = _resolve_asset_href(sibling, self.signer)

        return AssetInfo(
            url=_resolve_asset_href(annotation_asset, self.signer),
            name=asset_name,
            media_type=annotation_asset.media_type,
            reader_options=reader_options,
            method_options={},
        )

    def _get_asset_info(self, asset: AssetType) -> AssetInfo:  # noqa: C901
        """Custom version of rio_tiler.io.stac.STACReader()._get_asset_info
        which add support for nodata.

        """
        if isinstance(asset, str):
            asset = {"name": asset}

        if not asset.get("name"):
            raise ValueError("asset dictionary does not have `name` key")

        asset_name = asset["name"]

        if asset_name in self._derived_bands:
            return self._get_derived_asset_info(asset_name)

        if asset_name not in self.assets:
            raise InvalidAssetName(
                f"'{asset_name}' is not valid, should be one of "
                f"{sorted(set(self.assets) | set(self._derived_bands))}"
            )

        asset_info = self.input.assets[asset_name]
        extras = asset_info.extra_fields

        reader_options, method_options = self._get_options(asset, asset_info)

        asset_modified = "expression" in method_options

        info = AssetInfo(
            url=_resolve_asset_href(asset_info, self.signer),
            name=asset_name,
            media_type=asset_info.media_type,
            reader_options=reader_options,
            method_options=method_options,
        )

        if not asset_modified:
            info["metadata"] = extras

        # https://github.com/stac-extensions/file
        if head := extras.get("file:header_size"):
            info["env"] = {"GDAL_INGESTED_BYTES_AT_OPEN": head}

        # https://github.com/stac-extensions/raster
        if bands := (extras.get("bands") or extras.get("raster:bands")):
            if not asset_modified:
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
                info["method_options"]["nodata"] = nodata_values[0]

        # Extract nodata from asset level if not found in raster:bands.
        # Asset-level nodata is common in STAC catalogs like Copernicus Sentinel-2
        # where each asset (e.g., B04.tif) has a "nodata": 0 field.
        if "nodata" not in info["method_options"]:
            asset_nodata = extras.get("nodata")
            if asset_nodata is not None:
                info["method_options"]["nodata"] = asset_nodata

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
                        indexes=method_options.get("indexes"),
                        statistics=asset_info.get("dataset_statistics"),
                    )

                    metadata = data.metadata or {}
                    if m := asset_info.get("metadata"):
                        metadata.update(m)
                    data.metadata = {asset_name: metadata}

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

    # Built lazily, only if a requested name turns out not to be a real asset
    # -- the common (no derived bands requested) case pays nothing extra.
    item_asset_facts: Optional[List[Tuple[str, Optional[str], Sequence[str]]]] = None
    sibling_candidates: Optional[List[SiblingCandidateFacts]] = None
    collection_id = item.collection_id or ""

    for band_name in assets_to_process:
        resolution_asset_name = band_name

        if band_name not in item.assets:
            # Not a real asset -- maybe a band-source-derived name (ADR
            # 0002 S2.4). A derived band shares its sibling raster asset's
            # grid, so fall back to that asset's resolution rather than
            # silently contributing none (which would leave a derived-only
            # request's output dimensions defaulting to 1024x1024).
            if item_asset_facts is None:
                item_asset_facts = [
                    (key, a.media_type, a.roles or []) for key, a in item.assets.items()
                ]
                sibling_candidates = [
                    (key, a.media_type, a.roles or [], a.extra_fields.get("gsd"))
                    for key, a in item.assets.items()
                ]
            resolved = resolve_band(
                collection_id,
                band_name,
                item_asset_facts,
                BAND_SOURCES,
                sibling_candidates=sibling_candidates,
            )
            if resolved is None or not resolved.sibling_key:
                continue
            resolution_asset_name = resolved.sibling_key
            if resolution_asset_name not in item.assets:
                continue

        asset = item.assets[resolution_asset_name]
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

        # Keyed by the originally requested name, even when the resolution
        # came from a sibling -- callers (e.g. pixel-limit accounting) count
        # distinct requested bands, not distinct assets read.
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
        native_width = max(1, int(round((bbox[2] - bbox[0]) / x_resolution)))
        native_height = max(1, int(round((bbox[3] - bbox[1]) / y_resolution)))
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

                item_datetime = src_dst.input.datetime.isoformat()
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

    Note:
        This path opens assets to read their projection metadata, so it needs the
        same credential the read path does. It gets it the same way: from the key
        the item was stamped with at ingest (ADR 0005 S2.2).
    """
    # Get bounds CRS, target CRS, and bbox
    bounds_crs, output_crs, target_bbox = _get_target_crs_bbox(
        items, spatial_extent, target_crs
    )

    # Reproject bbox to output CRS for resolution/dimension calculations
    if bounds_crs != output_crs:
        output_bbox = list(
            transform_bounds(bounds_crs, output_crs, *target_bbox, densify_pts=21)
        )
    else:
        output_bbox = target_bbox

    # Get resolutions for each datetime and band
    cube_resolutions = _get_cube_resolutions(items, output_crs, output_bbox, bands)

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

    # Calculate dimensions using bbox in output CRS (same CRS as resolution)
    width, height = _calculate_dimensions(
        output_bbox, x_resolution, y_resolution, width, height
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


def _asset_extra_fields(item: Any, band: str) -> Dict[str, Any]:
    """Return the STAC extra fields for ``band`` from ``item`` (dict or pystac.Item)."""
    if isinstance(item, dict):
        return item.get("assets", {}).get(band, {}) or {}
    assets = getattr(item, "assets", None)
    if not assets or band not in assets:
        return {}
    return dict(getattr(assets[band], "extra_fields", {}) or {})


def _band_scale_offset(asset: Dict[str, Any]) -> Tuple[float, float]:
    """Extract (scale, offset) for a band from STAC asset metadata.

    Looks at the asset level first (``raster:scale``/``raster:offset``), then the
    raster-extension per-band variants. Defaults to ``(1.0, 0.0)`` (i.e. identity,
    e.g. for Sentinel-2 SCL which carries no scale/offset).
    """
    scale = asset.get("raster:scale")
    offset = asset.get("raster:offset")
    if scale is None or offset is None:
        bands = asset.get("raster:bands") or asset.get("bands")
        if bands:
            b0 = bands[0]
            if scale is None:
                scale = b0.get("scale", b0.get("raster:scale"))
            if offset is None:
                offset = b0.get("offset", b0.get("raster:offset"))
    return (
        float(scale) if scale is not None else 1.0,
        float(offset) if offset is not None else 0.0,
    )


def _apply_scale_offset(
    img: ImageData, item: Any, assets: Optional[Sequence[str]]
) -> ImageData:
    """Apply per-band STAC ``raster:scale``/``raster:offset`` to ``img``.

    Returns physical values (e.g. reflectance) as ``float32``. Bands without
    scale/offset metadata (default 1/0) are left unchanged, so e.g. Sentinel-2 SCL
    keeps its integer class values. The nodata mask is preserved.

    No-ops (returns ``img`` unchanged, keeping its dtype) when ``assets`` is missing,
    its length does not match the band count, or every band is identity (1/0).
    """
    if not assets:
        return img

    nbands = img.array.shape[0]
    if len(assets) != nbands:
        logger.warning(
            "Cannot apply scale/offset: %d band(s) but %d asset name(s); skipping.",
            nbands,
            len(assets),
        )
        return img

    pairs = [_band_scale_offset(_asset_extra_fields(item, b)) for b in assets]
    if all(scale == 1.0 and offset == 0.0 for scale, offset in pairs):
        return img

    scales = numpy.array([s for s, _ in pairs])
    offsets = numpy.array([o for _, o in pairs])
    # In-place unscale on a float32 copy, mirroring rio-tiler's Reader: casting the
    # array to float32 (then ``out=`` float32) keeps the result float32 regardless
    # of the scale/offset dtype, avoids extra allocations, and preserves the mask.
    data = cast(numpy.ma.MaskedArray, img.array.astype("float32", casting="unsafe"))
    numpy.multiply(data, scales.reshape((-1, 1, 1)), out=data, casting="unsafe")
    numpy.add(data, offsets.reshape((-1, 1, 1)), out=data, casting="unsafe")

    return ImageData(
        data,
        assets=img.assets,
        crs=img.crs,
        bounds=img.bounds,
        band_names=img.band_names,
        band_descriptions=img.band_descriptions,
        metadata=img.metadata,
    )


def _inherit_derived_band_masks(
    img: ImageData,
    derived_bands: Dict[str, ResolvedBand],
    requested: Sequence[str],
) -> ImageData:
    """Force each derived band's mask to match its sibling raster band's mask.

    A derived (band-source) value is honestly defined over the whole grid --
    unlike a raster band, it has no nodata region of its own. But
    ``ImageData._mask`` is ``logical_or.reduce(~array.mask)``: a pixel counts
    as valid if *any* band is unmasked. Left alone, an honestly-unmasked
    derived band would make a slice's nodata region report as valid to
    ``img.mask``, GeoTIFF nodata/alpha and ``save_result`` (this is the same
    trap `sar_backscatter`'s own ``mask`` band documents, ADR
    0002 S2.4). Values stay intact in ``array.data`` -- only the mask changes.

    If a derived band's sibling was not itself requested, there is nothing to
    inherit and that band's own mask (fully valid, as computed) is left alone
    -- a derived-only request has no raster band to borrow a mask from.
    """
    if not derived_bands:
        return img

    index_by_name = {name: i for i, name in enumerate(requested)}
    mask = numpy.ma.getmaskarray(img.array).copy()
    changed = False

    for band_name, idx in index_by_name.items():
        resolved = derived_bands.get(band_name)
        if resolved is None or not resolved.sibling_key:
            continue
        sibling_idx = index_by_name.get(resolved.sibling_key)
        if sibling_idx is None:
            continue
        mask[idx] = mask[sibling_idx]
        changed = True

    if not changed:
        return img

    img.array = numpy.ma.MaskedArray(img.array.data, mask=mask)
    return img


def _reader(item: Dict[str, Any], bbox: BBox, **kwargs: Any) -> ImageData:
    """
    Read a STAC item and return an ImageData object.

    Args:
        item: STAC item dictionary (converted to pystac.Item by SimpleSTACReader)
        bbox: Bounding box to read
        **kwargs: Additional keyword arguments to pass to the reader

    Returns:
        ImageData object with cutline_mask set from item geometry if available

    Note:
        Any credential these hrefs need comes from ``item`` itself, stamped at
        ingest and resolved inside ``SimpleSTACReader``. Because the retry loop
        below rebuilds the reader, a token that expired mid-read is re-minted
        rather than reused (docs/adr/0005-asset-href-signing.md S3.1).
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

                requested = kwargs.get("assets")
                if requested:
                    # getattr, not a direct attribute access: some tests
                    # substitute a minimal stand-in for SimpleSTACReader that
                    # does not carry this (SimpleSTACReader-internal) attribute
                    # -- treat that the same as "no derived bands".
                    img = _inherit_derived_band_masks(
                        img, getattr(src_dst, "_derived_bands", {}), requested
                    )

                # Apply STAC raster:scale/raster:offset (per band) so bands are
                # returned as physical values (e.g. reflectance) instead of raw DN.
                # Runs inside the lazy task — only when a slice is actually read.
                if processing_settings.apply_scale_offset:
                    img = _apply_scale_offset(img, item, kwargs.get("assets"))

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
