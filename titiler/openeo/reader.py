"""titiler-openeo custom reader."""

import logging
import warnings
from contextlib import ExitStack
from datetime import datetime
from functools import partial
from threading import Lock
from typing import (
    Any,
    Callable,
    Dict,
    List,
    Mapping,
    Optional,
    Sequence,
    Tuple,
    Type,
    Union,
    cast,
)

import attr
import numpy
import pystac
import rasterio
from morecantile import TileMatrixSet
from openeo_pg_parser_networkx.pg_schema import BoundingBox
from pystac.extensions.projection import ProjectionExtension
from rasterio.enums import MaskFlags
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

from .assetbands import ResolvedAssetBand, asset_band_facts, resolve_asset_bands
from .bandsources import (
    BAND_SOURCES,
    ResolvedBand,
    SiblingCandidateFacts,
    derive_bands,
    resolve_band,
)
from .cancellation import cancellable_sleep, raise_if_cancelled
from .errors import OutputLimitExceeded
from .scaleoffset import PLUGINS as SCALE_OFFSET_PLUGINS
from .scaleoffset import ProductScaleOffset, ScaleOffsetPlugin, find_metadata_asset
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
    #: The bounding box in ``bounds_crs``, as requested or as the union of the
    #: items' bounds.
    bbox: List[float]
    #: ``bbox`` reprojected to ``crs`` -- the grid the output is read on.
    output_bbox: List[float]


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

    **Nodata fallback.** ``fallback_nodata`` is a callable that returns a
    nodata value (or None). It is called after the dataset is open, and only
    when the header has no nodata, alpha band or mask and no ``nodata`` option
    is set. Its value then goes into ``options``, so the read and the warp mask
    it. ``SimpleSTACReader`` sets it from a scale/offset plugin's product
    metadata (#463, docs/adr/0009-scale-offset-sources.md S2.5).

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

    #: See "Nodata fallback" above. Called at most one time, after the open.
    fallback_nodata: Optional[Callable[[], Optional[float]]] = attr.ib(
        default=None, kw_only=True
    )

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

        if (
            self.fallback_nodata is not None
            and self.options.get("nodata") is None
            and self.dataset.nodata is None
            and all(
                flags == [MaskFlags.all_valid] for flags in self.dataset.mask_flag_enums
            )
        ):
            nodata = self.fallback_nodata()
            if nodata is not None:
                self.options = {**self.options, "nodata": nodata}


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


def _to_pystac_item(item: Union[pystac.Item, Dict[str, Any]]) -> pystac.Item:
    """Return ``item`` as a ``pystac.Item``.

    `load_stac` and `load_url` hand `_reader` plain STAC dicts, while
    `load_collection` hands it `pystac.Item`s (issue #383).
    """
    if isinstance(item, dict):
        return pystac.Item.from_dict(item)
    return item


def _item_datetime(item: pystac.Item) -> datetime:
    """Return the item's ``datetime``, or its ``start_datetime`` if that is null.

    STAC allows ``"datetime": null`` when ``start_datetime`` and
    ``end_datetime`` are set, and pystac then requires both (issue #472).
    """
    return item.datetime or item.common_metadata.start_datetime


@attr.s
class SimpleSTACReader(MultiBaseReader):
    """Simplified STAC Reader.

    This is the class a backend subclasses when it needs another per-asset
    reader (e.g. one that routes Zarr media types to its own reader): override
    `_get_reader`/`_get_options`, then pass the subclass as ``reader_cls`` to
    `LoadCollection`/`LoadStac`. Every read and every dimension estimate is
    built with it; nothing in this module needs to be copied
    (issue #379, docs/adr/0011-backend-reader-seam.md).
    """

    input: pystac.Item = attr.ib(converter=_to_pystac_item)

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

    #: Bands published *inside* one of this item's own assets (e.g. EOPF's
    #: Sentinel-2 `reflectance` asset, holding all twelve bands), precomputed
    #: once from the item's own `bands` metadata -- no registry, no I/O. See
    #: titiler/openeo/assetbands.py. Checked in `_get_asset_info` after
    #: `_derived_bands`: a band-source rule already matched a specific asset,
    #: so it must not be shadowed by a same-named inner band.
    _inner_bands: Dict[str, ResolvedAssetBand] = attr.ib(init=False, factory=dict)

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

        self._inner_bands = resolve_asset_bands(asset_band_facts(self.input.assets))

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

            common_to_variable = _band_lookup(stac_bands)
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
            # Not a real asset key -- maybe a band published *inside* one
            # (titiler/openeo/assetbands.py), e.g. EOPF's `reflectance` holding
            # `b01`..`b12`. Rewritten to the owning asset plus a `bands` option,
            # which `_get_options` below already knows how to turn into
            # `indexes` -- the same mechanism a caller-supplied `bands` list
            # uses today, just reached from a bare name instead.
            resolved = self._inner_bands.get(asset_name)
            if resolved is None:
                raise InvalidAssetName(
                    f"'{asset_name}' is not valid, should be one of "
                    f"{sorted(set(self.assets) | set(self._derived_bands) | set(self._inner_bands))}"
                )
            if resolved.whole_asset:
                # The common name of a single-band asset: read all of it.
                asset = {**asset, "name": resolved.asset_key}
            else:
                asset = {
                    **asset,
                    "name": resolved.asset_key,
                    "bands": [resolved.band_name],
                }
            asset_name = resolved.asset_key

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
                # `is None`, not `or`: 0 is a valid nodata value (Sentinel-2).
                nodata = b.get("nodata")
                if nodata is None:
                    nodata = b.get("raster:nodata")
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

        # Last, a scale/offset plugin's product metadata (#463): Planetary
        # Computer's Sentinel-2 L2A gives the NODATA DN only in
        # `MTD_MSIL2A.xml`. Not a method option: the reader uses it only when
        # the raster header gives no nodata either, and fetches the file only
        # then (`OpenEOReader.fallback_nodata`).
        if (
            "nodata" not in info["method_options"]
            and issubclass(self.reader, OpenEOReader)
            and self._nodata_plugins(extras)
        ):
            info["reader_options"]["fallback_nodata"] = partial(
                self._plugin_nodata, extras
            )

        return info

    def _nodata_plugins(
        self, extras: Mapping[str, Any]
    ) -> List[Tuple[ScaleOffsetPlugin, Callable[[str, str, Any], Optional[float]]]]:
        """The deployment's plugins for this collection that give nodata for an asset.

        Only the plugins listed for this collection (`_scale_offset_sources`),
        and only for an asset the plugin knows (its ``band_key`` is not None):
        e.g. SCL is left as it is. Needs no fetch.
        """
        plugins = []
        for name in _scale_offset_sources(self.input):
            plugin = SCALE_OFFSET_PLUGINS.get(name)
            if plugin is None or plugin.nodata is None:
                continue
            if plugin.band_key(extras) is None:
                continue
            plugins.append((plugin, plugin.nodata))
        return plugins

    def _plugin_nodata(self, extras: Mapping[str, Any]) -> Optional[float]:
        """The nodata DN that the first of `_nodata_plugins` gives, or None."""
        for plugin, loader in self._nodata_plugins(extras):
            nodata = _load_from_plugin(
                plugin,
                self.input,
                loader,
                self.signer,
                self.band_source_fetcher,
            )
            if nodata is not None:
                return nodata
        return None

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


#: The asset reader class a read or a dimension estimate is built with. Every
#: function taking one accepts ``None`` and resolves it to `SimpleSTACReader`
#: *at call time*: tests monkeypatch ``reader.SimpleSTACReader``, which a
#: def-time default would freeze (ADR 0011 S2.3).
ReaderCls = Type[SimpleSTACReader]


def _default_reader_cls(reader_cls: Optional[ReaderCls]) -> ReaderCls:
    """``reader_cls`` or the module's `SimpleSTACReader`, looked up now."""
    return reader_cls if reader_cls is not None else SimpleSTACReader


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


def _gsd_resolution(
    gsd: Any, crs: Optional[rasterio.crs.CRS]
) -> Tuple[Optional[float], Optional[float]]:
    """Turn a STAC `gsd` into an (x, y) resolution in `crs` units.

    `gsd` is a ground distance in metres (STAC common metadata), but callers
    treat the result as already in the CRS's own units -- `_reproject_resolution`
    passes it straight through. That only holds for a projected CRS, so a
    geographic (or unknown) CRS gets no resolution rather than a metre value
    read as degrees.
    """
    if not gsd or crs is None or not crs.is_projected:
        return None, None
    return abs(float(gsd)), abs(float(gsd))


def _get_asset_resolution(
    item: pystac.Item,
    asset: pystac.Asset,
    asset_proj_ext: Optional[ProjectionExtension],
    src_dst: SimpleSTACReader,
    asset_crs: Optional[rasterio.crs.CRS] = None,
) -> Tuple[Optional[float], Optional[float]]:
    """Get x and y resolutions from asset metadata.

    Args:
        item: STAC item
        asset: STAC asset
        asset_proj_ext: Asset's projection extension
        src_dst: SimpleSTACReader instance
        asset_crs: The asset's resolved CRS; the `gsd` fallback is used only
            when it is projected

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

    # Last resort: the asset's own declared ground sample distance (STAC common
    # metadata `gsd`), in the asset CRS's ground units. Catches an asset that
    # carries neither `proj:transform`/`proj:shape` nor a usable item-level
    # transform -- e.g. EOPF's `AOT_10m`/`SCL_20m`/`WVP_10m`, which declare only
    # `gsd`. Without this, such an asset contributes no resolution at all and
    # `_estimate_output_dimensions` silently ignores it.
    return _gsd_resolution(asset.extra_fields.get("gsd"), asset_crs)


def _get_assets_resolutions(
    item: pystac.Item,
    src_dst: SimpleSTACReader,
    bands: Optional[list[str]] = None,
    assets: Optional[Sequence[AssetType]] = None,
) -> Dict[str, tuple[float, float, rasterio.crs.CRS]]:
    """Get x and y resolutions and CRS for each band from STAC assets.

    Args:
        item: STAC item dictionary
        src_dst: SimpleSTACReader instance
        bands: Optional list of band names to filter assets
        assets: What is read for each of ``bands``, one entry per band, as
            `LoadCollection._bands_to_assets` returns it (e.g. ``B01|bands=b1``
            read as ``{"name": "B01", "bands": ["b1"]}``). The resolution comes
            from the asset each entry names; the result stays keyed by the
            band name. None: each band name is the asset name.

    Returns:
        Dictionary mapping band names to (x_resolution, y_resolution, crs) tuples
    """
    band_resolutions = {}
    # ``band name -> (asset name, gsd of the selected bands)``: what to look
    # up for each requested band.
    assets_to_process: Dict[str, Tuple[str, Optional[float]]]
    if not bands:
        assets_to_process = {key: (key, None) for key in item.get_assets()}
    elif assets is not None and len(assets) == len(bands):
        assets_to_process = {}
        for band, asset in zip(bands, assets):
            name = asset["name"] if isinstance(asset, dict) else asset
            gsd = _selection_gsd(asset, _asset_extra_fields(item, name))
            assets_to_process[band] = (name, gsd)
    else:
        assets_to_process = {band: (band, None) for band in bands}

    # Built lazily, only if a requested name turns out not to be a real asset
    # -- the common (no derived bands requested) case pays nothing extra.
    item_asset_facts: Optional[List[Tuple[str, Optional[str], Sequence[str]]]] = None
    sibling_candidates: Optional[List[SiblingCandidateFacts]] = None
    collection_id = item.collection_id or ""

    for band_name, (lookup_name, band_gsd) in assets_to_process.items():
        resolution_asset_name = lookup_name
        # ``band_gsd`` is set for a band selected inside an asset (an asset
        # dict, `_selection_gsd`) or resolved via `_inner_bands` below: its
        # own declared ground sample distance, which overrides the owning
        # asset's (a multi-band asset's own metadata describes just its finest
        # band -- EOPF's `reflectance` declares 10m even though `b01` is
        # native 20m).

        if lookup_name not in item.assets:
            # Not a real asset key. Prefer a band published *inside* one of
            # this item's own assets (titiler/openeo/assetbands.py) over a
            # band-source-derived name below: `src_dst._inner_bands` is
            # precomputed for this exact item, so this costs nothing extra to
            # check first, and the two mechanisms cannot both match one name.
            resolved_inner = src_dst._inner_bands.get(lookup_name)
            if resolved_inner is not None:
                resolution_asset_name = resolved_inner.asset_key
                band_gsd = resolved_inner.metadata.get("gsd")
            else:
                # Maybe a band-source-derived name instead (ADR 0002 S2.4). A
                # derived band shares its sibling raster asset's grid, so fall
                # back to that asset's resolution rather than silently
                # contributing none (which would leave a derived-only
                # request's output dimensions defaulting to 1024x1024).
                if item_asset_facts is None:
                    item_asset_facts = [
                        (key, a.media_type, a.roles or [])
                        for key, a in item.assets.items()
                    ]
                    sibling_candidates = [
                        (key, a.media_type, a.roles or [], a.extra_fields.get("gsd"))
                        for key, a in item.assets.items()
                    ]
                resolved = resolve_band(
                    collection_id,
                    lookup_name,
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

        # Get resolution: the band's own gsd when usable, else the asset's.
        x_res, y_res = _gsd_resolution(band_gsd, asset_crs)
        if x_res is None or y_res is None:
            x_res, y_res = _get_asset_resolution(
                item, asset, asset_proj_ext, src_dst, asset_crs
            )

        # Skip if we couldn't determine resolution
        if x_res is None or y_res is None:
            logger.debug(
                "No resolution for band %r (asset %r, crs %s) in item %r; "
                "it is ignored when estimating output dimensions",
                band_name,
                resolution_asset_name,
                asset_crs,
                item.id,
            )
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
    # A fresh instance, not the module-level one: the limit is read from the
    # environment at call time (tests set it with `monkeypatch.setenv`).
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
    readers: Sequence[SimpleSTACReader],
    spatial_extent: Optional[BoundingBox],
    target_crs: Optional[Union[int, str, rasterio.crs.CRS]] = None,
) -> Tuple[rasterio.crs.CRS, rasterio.crs.CRS, List[float]]:
    """Get bounds CRS, target CRS, and bbox from items and spatial extent.

    Args:
        readers: One opened reader per STAC item (see `_estimate_output_dimensions`)
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
    for src_dst in readers:
        item_bbox = src_dst.bounds
        if item_bbox and not spatial_extent:
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
            native_crs = _get_native_crs_from_item(src_dst.input, src_dst.crs)

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
    readers: Sequence[SimpleSTACReader],
    target_crs: rasterio.crs.CRS,
    target_bbox: List[float],
    bands: Optional[list[str]],
    assets: Optional[Sequence[AssetType]] = None,
) -> Dict[str, Dict[str, List[Tuple[float, float, List[float]]]]]:
    """Get resolutions for each datetime and band combination.

    ``readers`` are the opened readers `_estimate_output_dimensions` built, one
    per item; each reader's ``input`` is the item itself. ``assets`` as in
    `_get_assets_resolutions`.
    """
    cube_resolutions: Dict[str, Dict[str, List[Tuple[float, float, List[float]]]]] = {}

    for src_dst in readers:
        asset_resolutions = _get_assets_resolutions(
            src_dst.input, src_dst, bands, assets
        )
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

            item_datetime = _item_datetime(src_dst.input).isoformat()
            cube_resolutions.setdefault(item_datetime, {}).setdefault(
                band_name, []
            ).append((x_val, y_val, target_bbox))

    return cube_resolutions


def _estimate_output_dimensions(
    items: List[pystac.Item],
    spatial_extent: Optional[BoundingBox],
    bands: Optional[list[str]],
    width: Optional[int] = None,
    height: Optional[int] = None,
    check_max_pixels: bool = True,
    target_crs: Optional[Union[int, str, rasterio.crs.CRS]] = None,
    reader_cls: Optional[ReaderCls] = None,
    assets: Optional[Sequence[AssetType]] = None,
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
        reader_cls: The reader class to open each item with; `SimpleSTACReader`
            by default. A backend passes the same subclass it reads with, so the
            estimate sees the same bounds, CRS and inner bands the read will.
        assets: What is read for each of ``bands``, one entry per band
            (`LoadCollection._bands_to_assets`). Resolutions are looked up on
            these assets, and the pixel limit still counts ``bands``. None:
            each band name is the asset name.

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
    # One reader per item, built once and shared by both helpers below. Building
    # a `SimpleSTACReader` is not free: `__attrs_post_init__` resolves derived
    # and inner bands and, for SAR items, opens an asset header
    # (`_item_has_untrustworthy_proj`). The readers hold no file handles, so
    # keeping them all open for the duration of the estimate costs only memory.
    reader_cls = _default_reader_cls(reader_cls)
    with ExitStack() as stack:
        readers = [stack.enter_context(reader_cls(item)) for item in items]

        # Get bounds CRS, target CRS, and bbox
        bounds_crs, output_crs, target_bbox = _get_target_crs_bbox(
            readers, spatial_extent, target_crs
        )

        # Reproject bbox to output CRS for resolution/dimension calculations
        if bounds_crs != output_crs:
            output_bbox = list(
                transform_bounds(bounds_crs, output_crs, *target_bbox, densify_pts=21)
            )
        else:
            output_bbox = target_bbox

        # Get resolutions for each datetime and band
        cube_resolutions = _get_cube_resolutions(
            readers, output_crs, output_bbox, bands, assets
        )

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
        output_bbox=output_bbox,
    )


def _asset_extra_fields(item: Any, band: str) -> Dict[str, Any]:
    """Return the STAC extra fields for ``band`` from ``item`` (dict or pystac.Item)."""
    if isinstance(item, dict):
        return item.get("assets", {}).get(band, {}) or {}
    assets = getattr(item, "assets", None)
    if not assets or band not in assets:
        return {}
    return dict(getattr(assets[band], "extra_fields", {}) or {})


#: The per-band lists of an asset's STAC metadata, in the order of its bands.
_BAND_LISTS = ("raster:bands", "bands", "eo:bands")


def _narrow_to_band(fields: Dict[str, Any], index: int) -> Dict[str, Any]:
    """``fields`` with every per-band list cut to its 1-based band ``index``.

    So a helper that reads the first band (`_band_scale_offset`, a plugin's
    ``band_key``) reads band ``index``. A list too short for ``index`` becomes
    empty: that band has no per-band metadata.
    """
    narrowed = dict(fields)
    for key in _BAND_LISTS:
        values = fields.get(key)
        if isinstance(values, list):
            narrowed[key] = values[index - 1 : index]
    return narrowed


def _band_lookup(stac_bands: Sequence[Any]) -> Dict[str, int]:
    """``band name -> 1-based index`` for an asset's ``bands`` (``eo:bands``) list.

    The one place that resolves a ``bands`` selection: `SimpleSTACReader._get_options`
    uses it for the read, and `_asset_bands` for the post-read steps, so both
    always pick the same band.

    There is no standard for precedence between 'eo:common_name' and 'name'
    in STAC specification, so we will use 'eo:common_name' if it exists,
    otherwise fallback to 'name', and if not exist use the band index as last resource.
    The positional fallback key is stringified: `bands` values arrive as
    strings (the openEO `bands` argument, and rio-tiler's asset options),
    so an int key here can never be matched and the fallback was dead --
    requesting band "1" raised "not found in asset metadata" instead of
    selecting index 1.
    """
    lookup: Dict[str, int] = {}
    for ix, b in enumerate(stac_bands, 1):
        meta = b if isinstance(b, Mapping) else {}
        key = (
            meta.get("eo:common_name")
            or meta.get("common_name")
            or meta.get("name")
            or str(ix)
        )
        lookup[key] = ix
    return lookup


def _stac_bands(fields: Mapping[str, Any]) -> List[Any]:
    """The asset's ``bands`` list (``eo:bands`` for STAC 1.0), or []."""
    return list(fields.get("bands") or fields.get("eo:bands") or [])


def _selected_indexes(
    asset: AssetType, fields: Mapping[str, Any]
) -> Optional[List[Optional[int]]]:
    """The 1-based indexes of the bands that reading ``asset`` returns, in order.

    The same selection `SimpleSTACReader._get_options` makes: ``bands``
    (resolved with `_band_lookup`) wins over ``indexes``. ``[None]`` for a
    plain name or a dict without a selection: the whole asset, taken as one
    band. None for an ``expression``: its output bands do not map to source
    bands. A band name the asset does not have gives 0 (the read raises).
    """
    if not isinstance(asset, dict):
        return [None]
    if asset.get("expression"):
        return None
    if bands := asset.get("bands"):
        lookup = _band_lookup(_stac_bands(fields))
        return [lookup.get(band, 0) for band in bands]
    if indexes := asset.get("indexes"):
        return [indexes] if isinstance(indexes, int) else list(indexes)
    return [None]


def _asset_bands(
    item: Any, assets: Sequence[AssetType]
) -> Optional[List[Tuple[str, Optional[int]]]]:
    """One ``(asset name, 1-based band index)`` pair per band that reading ``assets`` returns.

    ``assets`` may mix plain names with rio-tiler asset dicts
    (``{"name": ..., "bands": [...]}``), which is what a backend's
    `LoadCollection._bands_to_assets` produces. A dict that selects several
    bands gives one pair per band, so the result is in output-band order --
    what the post-read steps in `_reader` key on. The index is None for a
    whole asset (`_selected_indexes`). None when an asset carries an
    ``expression``.
    """
    out: List[Tuple[str, Optional[int]]] = []
    for asset in assets:
        name = asset["name"] if isinstance(asset, dict) else asset
        indexes = _selected_indexes(asset, _asset_extra_fields(item, name))
        if indexes is None:
            return None
        out.extend((name, index) for index in indexes)
    return out


def _asset_band_fields(
    item: Any, assets: Sequence[AssetType]
) -> Optional[List[Tuple[str, Dict[str, Any]]]]:
    """One ``(asset name, STAC metadata)`` pair per band that reading ``assets`` returns.

    The bands of `_asset_bands`. A whole asset gives its metadata as is; a
    selected band gives the metadata narrowed to that band (`_narrow_to_band`),
    so a non-first band never gets the first band's scale/offset. None when
    an asset carries an ``expression``.
    """
    selection = _asset_bands(item, assets)
    if selection is None:
        return None
    out: List[Tuple[str, Dict[str, Any]]] = []
    for name, index in selection:
        fields = _asset_extra_fields(item, name)
        out.append((name, fields if index is None else _narrow_to_band(fields, index)))
    return out


def _selection_gsd(asset: AssetType, fields: Mapping[str, Any]) -> Optional[float]:
    """The finest per-band ``gsd`` of the bands ``asset`` selects, or None.

    A multi-band asset's own ``gsd`` describes just its finest band (EOPF's
    `reflectance` declares 10m, but its `b01` is native 20m), so a selection
    of `b01` must use the band's ``gsd``. None for a whole asset, or when no
    selected band declares one.
    """
    indexes = _selected_indexes(asset, fields)
    if not indexes or indexes == [None]:
        return None
    stac_bands = _stac_bands(fields)
    gsds = [
        float(stac_bands[i - 1]["gsd"])
        for i in indexes
        if i
        and 0 < i <= len(stac_bands)
        and isinstance(stac_bands[i - 1], Mapping)
        and stac_bands[i - 1].get("gsd") is not None
    ]
    return min(gsds) if gsds else None


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


def _scale_offset_sources(item: Any) -> List[str]:
    """Return the ordered scale/offset sources for ``item``'s collection.

    The master switch `apply_scale_offset` (off -> none), then the
    collection's list in `scale_offset_collections`, then the global
    `scale_offset_sources`. The collection id comes from the item itself, so
    an item without one (e.g. from `load_stac`) uses the global list.
    """
    settings = processing_settings
    if not settings.apply_scale_offset:
        return []

    if isinstance(item, dict):
        collection = item.get("collection")
    else:
        collection = getattr(item, "collection_id", None)

    if collection and collection in settings.scale_offset_collections:
        return list(settings.scale_offset_collections[collection])
    return list(settings.scale_offset_sources)


def _is_identity(pair: Tuple[float, float]) -> bool:
    return pair[0] == 1.0 and pair[1] == 0.0


#: ``plugin name -> per-band pairs``, or None when the plugin does not apply.
PluginPairs = Callable[[str], Optional[ProductScaleOffset]]


def _load_from_plugin(
    plugin: ScaleOffsetPlugin,
    item: pystac.Item,
    loader: Callable[[str, str, Any], Any],
    signer: Optional[HrefSigner],
    fetcher: Any,
) -> Any:
    """Call ``loader`` (one of ``plugin``'s) on the item's metadata asset.

    Finds the plugin's metadata asset on the item, signs its href with the
    reader's signer, and fetches it with the reader's band-source fetcher --
    the same path the derived-band readers use for their XML. The unsigned
    href is the plugin's cache key. None when the plugin does not apply.
    """
    asset = find_metadata_asset(plugin, item)
    if asset is None:
        return None
    return loader(
        _resolve_asset_href(asset), _resolve_asset_href(asset, signer), fetcher
    )


def _plugin_pairs_for(src_dst: Any) -> PluginPairs:
    """Load a plugin's pairs for the item ``src_dst`` reads (`_load_from_plugin`)."""
    item = getattr(src_dst, "input", None)

    def load(name: str) -> Optional[ProductScaleOffset]:
        if not isinstance(item, pystac.Item):
            return None
        plugin = SCALE_OFFSET_PLUGINS[name]
        return _load_from_plugin(
            plugin,
            item,
            plugin.load,
            getattr(src_dst, "signer", None),
            getattr(src_dst, "band_source_fetcher", None),
        )

    return load


def _apply_scale_offset(
    img: ImageData,
    item: Any,
    assets: Optional[Sequence[AssetType]],
    *,
    sources: Sequence[str] = ("stac",),
    plugin_pairs: Optional[PluginPairs] = None,
) -> ImageData:
    """Apply per-band scale/offset to ``img``.

    For each band, ``sources`` are tried in order and the first one with a
    scale/offset that is not 1/0 wins, so a band is never scaled two times:

    - ``"stac"``: the asset's ``raster:scale``/``raster:offset``;
    - ``"cog"``: the COG header scale/offset that rio-tiler reports on
      ``img.scales``/``img.offsets`` when it reads with ``unscale=False``;
    - a plugin name (`titiler.openeo.scaleoffset.PLUGINS`): the pairs that
      ``plugin_pairs(name)`` returns. It is called at most once per plugin,
      and only when the walk reaches the plugin for a band the plugin knows
      (its ``band_key`` is not None); ``None`` means the plugin does not apply
      to this item and is skipped. Errors raise.

    A band that no source covers stays as it is (identity).

    Returns physical values (e.g. reflectance) as ``float32``. Bands without
    scale/offset (default 1/0) are left unchanged, so e.g. Sentinel-2 SCL
    keeps its integer class values. The nodata mask is preserved.

    ``assets`` are the requested assets: names, or rio-tiler asset dicts whose
    ``indexes``/``bands`` pick bands of an asset (`_asset_band_fields`).

    No-ops (returns ``img`` unchanged, keeping its dtype) when ``assets`` is missing,
    an asset carries an ``expression``, the number of requested bands does not
    match the band count, or every band is identity (1/0).
    """
    if not assets or not sources:
        return img

    band_fields = _asset_band_fields(item, assets)
    if band_fields is None:
        logger.warning("Cannot apply scale/offset to an asset expression; skipping.")
        return img

    nbands = img.array.shape[0]
    if len(band_fields) != nbands:
        logger.warning(
            "Cannot apply scale/offset: %d band(s) but %d asset band(s); skipping.",
            nbands,
            len(band_fields),
        )
        return img

    img_scales, img_offsets = img.scales or [], img.offsets or []
    has_cog = len(img_scales) == nbands and len(img_offsets) == nbands

    loaded: Dict[str, Optional[ProductScaleOffset]] = {}

    def from_source(
        source: str, index: int, fields: Dict[str, Any]
    ) -> Tuple[float, float]:
        if source == "stac":
            return _band_scale_offset(fields)
        if source == "cog":
            if not has_cog:
                return (1.0, 0.0)
            return (float(img_scales[index]), float(img_offsets[index]))
        # Ask the plugin for the band's key first: that needs no fetch, so a
        # band the plugin knows nothing about (e.g. SCL) never loads its file.
        key = SCALE_OFFSET_PLUGINS[source].band_key(fields)
        if plugin_pairs is None or key is None:
            return (1.0, 0.0)
        if source not in loaded:
            loaded[source] = plugin_pairs(source)
        product = loaded[source]
        return product.get(key, (1.0, 0.0)) if product else (1.0, 0.0)

    pairs: List[Tuple[float, float]] = []
    for index, (_, fields) in enumerate(band_fields):
        pair = (1.0, 0.0)
        for source in sources:
            pair = from_source(source, index, fields)
            if not _is_identity(pair):
                break
        pairs.append(pair)

    if all(_is_identity(pair) for pair in pairs):
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

    ``requested`` names each band of ``img`` in order (`_asset_bands`). When
    the counts differ (a plain name for a multi-band asset), the positions are
    not known and the masks are left alone, so no band borrows the mask of
    another.
    """
    if not derived_bands:
        return img

    if len(requested) != img.count:
        logger.warning(
            "Cannot inherit derived band masks: %d band(s) but %d requested "
            "band name(s); skipping.",
            img.count,
            len(requested),
        )
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


def _reader(
    item: Union[pystac.Item, Dict[str, Any]],
    bbox: BBox,
    *,
    reader_cls: Optional[ReaderCls] = None,
    **kwargs: Any,
) -> ImageData:
    """
    Read a STAC item and return an ImageData object.

    Args:
        item: STAC item, as a ``pystac.Item`` or a plain dict
        bbox: Bounding box to read
        reader_cls: The reader class to open ``item`` with; `SimpleSTACReader`
            by default. This is the one supported way for a backend to change
            the asset reader -- it reaches here as an ordinary ``mosaic_reader``
            / ``create_tasks`` keyword, which is why it is keyword-only and
            consumed before ``**kwargs`` is forwarded to ``part()``
            (issue #379, docs/adr/0011-backend-reader-seam.md).
        **kwargs: Additional keyword arguments to pass to the reader

    Returns:
        ImageData object with cutline_mask set from item geometry if available

    Note:
        Any credential these hrefs need comes from ``item`` itself, stamped at
        ingest and resolved inside ``SimpleSTACReader``. Because the retry loop
        below rebuilds the reader, a token that expired mid-read is re-minted
        rather than reused (docs/adr/0005-asset-href-signing.md S3.1).
    """
    reader_cls = _default_reader_cls(reader_cls)
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
        # Before each read and each retry: stop if the client went away.
        raise_if_cancelled()
        try:
            with reader_cls(item) as src_dst:
                img = src_dst.part(bbox, **kwargs)

                requested = kwargs.get("assets")
                selection = _asset_bands(item, requested) if requested else None
                if selection:
                    # getattr, not a direct attribute access: some tests
                    # substitute a minimal stand-in for SimpleSTACReader that
                    # does not carry this (SimpleSTACReader-internal) attribute
                    # -- treat that the same as "no derived bands".
                    img = _inherit_derived_band_masks(
                        img,
                        getattr(src_dst, "_derived_bands", {}),
                        [name for name, _ in selection],
                    )

                # Apply scale/offset (per band, from the deployment's ordered
                # sources: STAC, a plugin's product metadata, the COG header)
                # so bands are returned as physical values (e.g. reflectance)
                # instead of raw DN. Runs inside the lazy task — only when a
                # slice is actually read.
                sources = _scale_offset_sources(item)
                if sources:
                    img = _apply_scale_offset(
                        img,
                        item,
                        requested,
                        sources=sources,
                        plugin_pairs=_plugin_pairs_for(src_dst),
                    )

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
            cancellable_sleep(retry_delay)
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
