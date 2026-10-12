"""Stac API backend."""

from threading import Lock
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set, Tuple, Union

import numpy
import pyproj
import pystac
from attrs import define, field
from cachetools import TTLCache, cached
from cachetools.keys import hashkey
from openeo_pg_parser_networkx.pg_schema import (
    BoundingBox,
    ParameterReference,
    TemporalInterval,
)
from pystac import Collection, Item
from pystac.extensions import datacube as dc
from pystac.extensions import eo
from pystac.extensions import item_assets as ia
from pystac_client import Client
from pystac_client.stac_api_io import StacApiIO
from rasterio.features import bounds as geometry_bounds
from rasterio.warp import transform_bounds
from rio_tiler.constants import MAX_THREADS
from rio_tiler.errors import TileOutsideBounds
from rio_tiler.models import ImageData
from rio_tiler.mosaic.methods import PixelSelectionMethod
from rio_tiler.mosaic.reader import mosaic_reader
from rio_tiler.tasks import create_tasks
from urllib3 import Retry

from .assetbands import ResolvedAssetBand, asset_band_facts, resolve_asset_bands
from .bandsources import BAND_SOURCES, derive_bands, describe_bands
from .errors import (
    ItemsLimitExceeded,
    NoDataAvailable,
    OutputLimitExceeded,
    ProcessParameterInvalid,
    STACLoadError,
    TemporalExtentEmpty,
    UnsupportedSTACObject,
)
from .processes.implementations.data_model import RasterStack, compute_cutline_mask
from .processes.implementations.spatial import _extract_geometries_from_mask
from .processes.implementations.utils import _props_to_datetime, to_rasterio_crs
from .reader import _estimate_output_dimensions, _reader
from .settings import CacheSettings, ProcessingSettings, PySTACSettings
from .signing import stamp_signer_key

pystac_settings = PySTACSettings()
cache_config = CacheSettings()
processing_settings = ProcessingSettings()

collections_cache: TTLCache = TTLCache(
    maxsize=cache_config.maxsize, ttl=cache_config.ttl
)
collection_cache: TTLCache = TTLCache(
    maxsize=cache_config.maxsize, ttl=cache_config.ttl
)

#: Media types that indicate a packaged archive rather than a single raster
#: asset. CDSE's Sentinel-1 GRD ``Product`` asset has role ``data`` and media
#: type ``application/zip``, which without this exclusion gets advertised as
#: a spectral band even though it is not a raster (docs/adr/0002-band-sources.md
#: S1.2, consequence 4).
_ARCHIVE_MEDIA_TYPES = frozenset({"application/zip"})


#: Spectral fields of a band, by their eo extension v1.x names.
_SPECTRAL_FIELDS = ("common_name", "center_wavelength", "full_width_half_max")


def _is_data_asset(media_type: Optional[str], roles: Optional[Sequence[str]]) -> bool:
    """Whether an asset is read as raster data, i.e. can give cube bands."""
    return "data" in (roles or ()) and media_type not in _ARCHIVE_MEDIA_TYPES


def _band_dimension_values(collection: Dict) -> List[str]:
    """The values of the first ``bands`` dimension of ``cube:dimensions``."""
    dimensions = collection.get("cube:dimensions")
    if not isinstance(dimensions, dict):
        return []
    for dimension in dimensions.values():
        if isinstance(dimension, dict) and dimension.get("type") == "bands":
            return list(dimension.get("values") or [])
    return []


def _band_fields(band: Mapping[str, Any]) -> Dict[str, Any]:
    """The metadata of one STAC band object (STAC 1.1 ``bands`` or eo v1.x
    ``eo:bands``), under the eo v1.x names plus ``description``/``gsd``."""
    fields: Dict[str, Any] = {}
    for k in _SPECTRAL_FIELDS:
        value = band.get(f"eo:{k}")
        if value is None:
            value = band.get(k)
        if value is not None:
            fields[k] = value
    for k in ("description", "gsd"):
        if band.get(k) is not None:
            fields[k] = band[k]
    return fields


def _upstream_band_fields(summaries: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Band metadata from the upstream ``summaries.bands``/``summaries.eo:bands``,
    by band name."""
    upstream: Dict[str, Dict[str, Any]] = {}
    for key in ("bands", "eo:bands"):
        for band in summaries.get(key) or []:
            if isinstance(band, dict) and band.get("name"):
                fields = upstream.setdefault(band["name"], {})
                for k, v in _band_fields(band).items():
                    fields.setdefault(k, v)
    return upstream


def _summary_entries(
    name: str, fields: Dict[str, Any]
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """One band as a ``summaries.bands`` (STAC 1.1) entry and as a
    ``summaries.eo:bands`` (eo extension v1.x) entry."""
    entry: Dict[str, Any] = {"name": name}
    eo_entry: Dict[str, Any] = {"name": name}
    if "description" in fields:
        entry["description"] = eo_entry["description"] = fields["description"]
    for k in _SPECTRAL_FIELDS:
        if k in fields:
            entry[f"eo:{k}"] = eo_entry[k] = fields[k]
    if "gsd" in fields:
        entry["gsd"] = fields["gsd"]
    return entry, eo_entry


def _asset_band_fields(asset: Dict[str, Any]) -> Dict[str, Any]:
    """Band metadata for a band that is an ``item_assets`` key."""
    asset_bands = asset.get("bands") or asset.get("eo:bands") or []
    # The spectral fields of a band apply to the asset only when it holds
    # exactly one band; a composite (e.g. a true-colour TCI) gets none.
    fields = _band_fields(asset_bands[0]) if len(asset_bands) == 1 else {}
    description = asset.get("description") or asset.get("title")
    if description:
        fields["description"] = description
    if "gsd" not in fields and asset.get("gsd") is not None:
        fields["gsd"] = asset["gsd"]
    return fields


def _inner_band_fields(
    resolved: ResolvedAssetBand,
    asset: Dict[str, Any],
    item_assets: Dict[str, Any],
) -> Dict[str, Any]:
    """Band metadata for a band inside a multi-band asset.

    The description always names the asset. Planetary Computer's ``visual``
    asset, for example, declares its bands as ``B04``/``B03``/``B02`` with
    the spectral metadata of those bands, but holds an 8-bit true-colour
    rendering of them: ``red`` is not the ``B04`` asset.
    """
    fields = _band_fields(resolved.metadata)
    if "gsd" not in fields and asset.get("gsd") is not None:
        fields["gsd"] = asset["gsd"]

    raw_name = resolved.metadata.get("name")
    text = f"Band '{resolved.band_name}'"
    if raw_name and raw_name != resolved.band_name:
        text += f" (STAC name '{raw_name}')"
    text += f" of the multi-band asset '{resolved.asset_key}'"
    asset_label = asset.get("title") or asset.get("description")
    if asset_label:
        text += f" ({asset_label})"
    data_type = resolved.metadata.get("data_type")
    if data_type:
        text += f", data type {data_type}"
    text += "."
    for other in (raw_name, resolved.band_name):
        if other and other != resolved.asset_key and other in item_assets:
            text += f" It is not the '{other}' asset."
            break
    if fields.get("description"):
        text = f"{fields['description']}. {text}"
    fields["description"] = text
    return fields


def _geojson_spatial_extent(
    geojson: Dict[str, Any],
    process_id: str,
) -> Tuple[BoundingBox, List[Dict[str, Any]]]:
    """Return the bounding box and the polygons of a GeoJSON spatial extent.

    openEO `load_collection` and `load_stac`: the data is loaded for the
    bounding box of the geometries, and the pixels outside the polygons are set
    to no data. GeoJSON coordinates are in EPSG:4326. Empty geometries are
    ignored.
    """
    try:
        geometries = _extract_geometries_from_mask(geojson)
    except ValueError as e:
        raise ProcessParameterInvalid(
            f"Parameter 'spatial_extent' in process '{process_id}': {e}"
        ) from e

    geometries = [geom for geom in geometries if geom.get("coordinates")]
    if not geometries:
        raise ProcessParameterInvalid(
            f"Parameter 'spatial_extent' in process '{process_id}': "
            "the GeoJSON contains no Polygon or MultiPolygon geometry."
        )

    west, south, east, north = zip(*(geometry_bounds(geom) for geom in geometries))
    bbox = BoundingBox(
        west=min(west), south=min(south), east=max(east), north=max(north)
    )
    return bbox, geometries


def _mask_outside_polygons(img: ImageData, polygons: List[Dict[str, Any]]) -> ImageData:
    """Set the pixels whose centre is outside the polygons to no data."""
    outside = compute_cutline_mask(polygons, img.width, img.height, img.bounds, img.crs)
    img.array[:, outside] = numpy.ma.masked
    return img


@define
class stacApiBackend:
    """PySTAC-Client Backend."""

    url: str = field()
    exclude_collections: list = field(factory=list)
    _client_cache: Client = field(default=None, init=False)

    @property
    def client(self) -> Client:
        """Return a PySTAC-Client."""
        if not self._client_cache:
            stac_api_io = StacApiIO(
                max_retries=Retry(
                    total=pystac_settings.retry,
                    backoff_factor=pystac_settings.retry_factor,
                ),
            )
            self._client_cache = Client.open(self.url, stac_io=stac_api_io)
        return self._client_cache

    def ping(self, timeout: float = 2.0) -> None:
        """Verify the STAC API root is reachable. Raises on failure."""
        # Use the pystac_client StacApiIO underlying session for a cheap GET
        # against the API root.
        session = self.client._stac_io.session  # type: ignore[attr-defined]
        response = session.get(self.url, timeout=timeout)
        response.raise_for_status()

    @cached(  # type: ignore
        collections_cache,
        key=lambda self, **kwargs: hashkey(self.url, **kwargs),
        lock=Lock(),
    )
    def get_collections(self, **kwargs) -> List[Dict]:
        """Return List of STAC Collections."""
        collections: List[Dict] = []
        for collection in self.client.get_collections():
            if collection.id in self.exclude_collections:
                continue

            collection = self.add_version_if_missing(collection)
            collection = self.add_data_cubes_if_missing(collection)

            col_dict = collection.to_dict()
            self._fix_collection(col_dict)
            collections.append(col_dict)

        return collections

    def _fix_collection(self, collection: Dict) -> None:
        self._normalize_summaries(collection)
        self._sync_band_summaries(collection)
        return

    @staticmethod
    def _sync_band_summaries(collection: Dict) -> None:
        """Rebuild summaries.bands and summaries.eo:bands from cube:dimensions.

        openEO API 1.3.0 makes ``cube:dimensions`` required and its band
        dimension the list of band names; the band fields in ``summaries``
        must describe the same bands, in the same order. Clients rely on
        this: openEO Studio and the openEO Web Editor take their band picker
        from ``summaries.bands``, and the openEO Python client compares
        ``summaries.eo:bands`` with ``cube:dimensions`` (issue #440).

        The band dimension (built by ``getdimensions``, or published upstream)
        is the single source of truth. Both summaries get exactly one entry
        per band-dimension value, with the metadata that exists for it --
        resolved with the same precedence as the read path
        (``SimpleSTACReader._get_asset_info``):

        1. an ``item_assets`` key: the asset's title/description and gsd,
           plus the spectral fields of its band if it has exactly one;
        2. a band inside a multi-band asset (titiler/openeo/assetbands.py):
           that band's own metadata, and a description that names the asset;
        3. a band derived by a band source (docs/adr/0002-band-sources.md):
           the band source's description.

        Upstream ``summaries.eo:bands``/``summaries.bands`` entries fill
        missing fields, matched by name only: they never decide the list.
        A collection without a band dimension is left unchanged.

        ``summaries.bands`` uses the STAC 1.1 field names
        (``eo:common_name``, ...); ``summaries.eo:bands`` uses the eo
        extension v1.x shape (``common_name``, ...).
        """
        band_names = _band_dimension_values(collection)
        if not band_names:
            return

        summaries = collection.get("summaries")
        if not isinstance(summaries, dict):
            summaries = {}
            collection["summaries"] = summaries

        item_assets = {
            key: asset
            for key, asset in (collection.get("item_assets") or {}).items()
            if isinstance(asset, dict)
        }
        data_assets = {
            key: asset
            for key, asset in item_assets.items()
            if _is_data_asset(asset.get("type"), asset.get("roles"))
        }
        inner_bands = resolve_asset_bands(asset_band_facts(data_assets))
        derived = describe_bands(
            collection.get("id") or "",
            [(k, a.get("type"), a.get("roles") or []) for k, a in item_assets.items()],
            BAND_SOURCES,
        )

        upstream = _upstream_band_fields(summaries)

        bands: List[Dict[str, Any]] = []
        eo_bands: List[Dict[str, Any]] = []
        for name in band_names:
            if name in item_assets:
                fields = _asset_band_fields(item_assets[name])
            elif name in inner_bands:
                resolved = inner_bands[name]
                fields = _inner_band_fields(
                    resolved, data_assets[resolved.asset_key], item_assets
                )
            elif name in derived:
                fields = {"description": derived[name]}
            else:
                fields = {}
            for k, v in upstream.get(name, {}).items():
                fields.setdefault(k, v)

            entry, eo_entry = _summary_entries(name, fields)
            bands.append(entry)
            eo_bands.append(eo_entry)

        summaries["bands"] = bands
        summaries["eo:bands"] = eo_bands

    @staticmethod
    def _normalize_summaries(collection: Dict):
        """Normalize summaries so scalar values are wrapped in a list.

        The openEO spec requires summary values to be a list, a stats
        object, or a JSON Schema.  Some STAC APIs return plain scalars
        (e.g. ``"processing:level": "L2A"``), which causes validation
        errors.
        """
        summaries = collection.get("summaries")
        if not isinstance(summaries, dict):
            return
        for key, value in summaries.items():
            if not isinstance(value, (list, dict)):
                summaries[key] = [value]

    def add_version_if_missing(self, collection: Collection):
        """Add version to collection if missing."""
        if not collection.ext.has("version"):
            collection.ext.add("version")
        if not collection.ext.version.version:
            collection.ext.version.version = "1.0.0"
        return collection

    def add_data_cubes_if_missing(self, collection: Collection):
        """Add datacubes extension to collection if missing."""
        if not collection.ext.has("cube"):
            dc.DatacubeExtension.add_to(collection)
            """ Add minimal dimensions """
            collection.ext.cube.apply(
                dimensions=self.getdimensions(collection),
                # variables=self.getvariables(collection),
            )

        return collection

    def getdimensions(self, collection: Collection) -> Dict[str, dc.Dimension]:
        """Get dimensions from collection"""
        dims = {}

        # Spatial extent
        if collection.extent.spatial.bboxes:
            dims["x"] = dc.Dimension.from_dict(
                {
                    "type": "spatial",
                    "axis": "x",
                    "extent": [
                        collection.extent.spatial.bboxes[0][0],
                        collection.extent.spatial.bboxes[0][2],
                    ],
                }
            )
            dims["y"] = dc.Dimension.from_dict(
                {
                    "type": "spatial",
                    "axis": "y",
                    "extent": [
                        collection.extent.spatial.bboxes[0][1],
                        collection.extent.spatial.bboxes[0][3],
                    ],
                }
            )

        # Temporal extent
        if collection.extent.temporal.intervals:
            dims["t"] = dc.Dimension.from_dict(
                {
                    "type": "temporal",
                    "extent": [
                        collection.extent.temporal.intervals[0][0],
                        collection.extent.temporal.intervals[0][1],
                    ],
                }
            )

        # bands
        if "item_assets" in collection.extra_fields:
            ia.ItemAssetsExtension.add_to(collection)

            band_names: Set[str] = set()
            asset_facts: List[Tuple[str, Optional[str], Sequence[str]]] = []
            data_assets: Dict[str, Any] = {}
            for key, asset in collection.ext.item_assets.items():
                roles = asset.roles or []
                asset_facts.append((key, asset.media_type, roles))
                if _is_data_asset(asset.media_type, roles):
                    # `.to_dict()`, not the `AssetDefinition` itself: it has no
                    # `.extra_fields` (unlike `pystac.Asset`), so `bands` is
                    # otherwise invisible to `assetbands.asset_band_facts`.
                    data_assets[key] = asset.to_dict()
                    band_names.add(key)

            # Assets publishing several bands at once advertise the bands, not
            # the asset key -- see titiler/openeo/assetbands.py. Single-band and
            # band-less assets are absent from this mapping and keep their key
            # above, which is what stops existing catalogues being renamed.
            inner_bands = resolve_asset_bands(asset_band_facts(data_assets))
            band_names.difference_update(r.asset_key for r in inner_bands.values())
            band_names.update(inner_bands)

            # Bands derived from non-raster assets (e.g. Sentinel-1 GRD's
            # calibration/noise annotation XML) -- see
            # docs/adr/0002-band-sources.md.
            band_names.update(derive_bands(collection.id, asset_facts, BAND_SOURCES))

            if band_names:
                dims["spectral"] = dc.Dimension.from_dict(
                    {
                        "type": "bands",
                        # Sorted rather than left as a set for a deterministic
                        # client-visible order (issue #280).
                        "values": sorted(band_names),
                    }
                )

        return dims

    def getvariables(self, collection: Collection) -> Dict[str, dc.Variable]:
        """Get variables from collection"""
        variables = {}
        if eo.EOExtension.has_extension(collection):
            eo_ext = eo.EOExtension.ext(collection)
            if eo_ext.bands:
                for band in eo_ext.bands:
                    variables[band.name] = dc.Variable.from_dict(
                        {
                            "name": band.name,
                            "description": band.description,
                            "unit": band.unit,
                            "data_type": band.data_type,
                        }
                    )
        return variables

    @cached(  # type: ignore
        collection_cache,
        key=lambda self, collection_id, **kwargs: hashkey(
            self.url, collection_id, **kwargs
        ),
        lock=Lock(),
    )
    def get_collection(self, collection_id: str, **kwargs) -> Dict:
        """Return STAC Collection"""
        if collection_id in self.exclude_collections:
            raise ValueError(f"Collection {collection_id} is not available.")

        collection = self.client.get_collection(collection_id)
        collection = self.add_version_if_missing(collection)
        collection = self.add_data_cubes_if_missing(collection)

        col_dict = collection.to_dict()
        self._fix_collection(col_dict)

        return col_dict

    def get_items(
        self,
        collections: List[str],
        ids: Optional[List[str]] = None,
        bbox: Optional[Sequence[float]] = None,
        intersects: Optional[Dict] = None,
        datetime: Optional[Union[str, Sequence[str]]] = None,
        query: Optional[Union[List, Dict]] = None,
        filter: Optional[Dict] = None,
        filter_lang: str = "cql2-json",
        sortby: Optional[Union[str, List[str]]] = None,
        fields: Optional[List[str]] = None,
        limit: Optional[int] = None,
        max_items: Optional[int] = None,
        **kwargs,
    ) -> List[Item]:
        """Return List of STAC Items."""
        limit = limit or 100
        max_items = max_items or 100

        items = self.client.search(
            collections=collections,
            ids=ids,
            bbox=bbox,
            intersects=intersects,
            datetime=datetime,
            query=query,
            filter=filter,
            filter_lang=filter_lang,
            sortby=sortby,
            fields=fields,
            limit=limit,
            max_items=max_items,
        )
        return list(items.items())


@define
class LoadCollection:
    """Backend Specific Collection loaders."""

    stac_api: stacApiBackend = field()

    #: Which signer this deployment's catalogue needs, injected by `main.py`
    #: rather than read from a module global so this class stays constructible
    #: in tests. `None` -- the default -- means every href is opened exactly as
    #: the catalogue published it (docs/adr/0005-asset-href-signing.md S2.2).
    #:
    #: Stamped onto every item this class retrieves rather than threaded down to
    #: the readers: the readers are built deep inside the mosaic and run on
    #: worker threads that no parameter reaches, and resolving the key at open
    #: time is what keeps a short-lived credential fresh across retries.
    signer_key: Optional[str] = field(default=None)

    def _resolve_parameter_reference(
        self,
        value: Any,
        named_parameters: Optional[dict] = None,
    ) -> tuple[bool, Optional[Any]]:
        """Resolve a parameter reference to its actual value.

        Handles both ParameterReference objects (from parser) and dict with
        'from_parameter' key (for backwards compatibility with direct calls).

        Args:
            value: The value to check and potentially resolve
            named_parameters: Dictionary of parameter values

        Returns:
            Tuple of (is_reference, resolved_value):
            - (True, resolved_value) if it was a reference and was resolved
            - (True, original_dict) if it was a dict reference that couldn't be resolved
            - (True, None) if it was a ParameterReference that couldn't be resolved
            - (False, None) if it wasn't a reference
        """
        param_name = None
        is_param_ref_object = False

        # Check if it's a ParameterReference object (from parser)
        if isinstance(value, ParameterReference):
            param_name = value.from_parameter
            is_param_ref_object = True
        # Check if it's a dict with 'from_parameter' key (backwards compatibility)
        elif isinstance(value, dict) and value.get("from_parameter"):
            param_name = value["from_parameter"]

        if param_name:
            if named_parameters and param_name in named_parameters:
                return (True, named_parameters[param_name])
            else:
                # Parameter reference found but not in named_parameters
                if is_param_ref_object:
                    # For ParameterReference objects, return None to skip the filter
                    return (True, None)
                else:
                    # For dict format, keep the original dict for backwards compatibility
                    return (True, value)

        return (False, None)

    def _get_items(
        self,
        id: str,
        spatial_extent: Optional[BoundingBox] = None,
        temporal_extent: Optional[TemporalInterval] = None,
        properties: Optional[dict] = None,
        fields: Optional[List[str]] = None,
        limit: Optional[int] = None,
        max_items: Optional[int] = None,
        named_parameters: Optional[dict] = None,
    ) -> List[Item]:
        fields = fields or [
            "assets",
            "id",
            "bbox",
            "geometry",
            "collection",
            "properties",
            "type",
            "stac_version",
            "stac_extensions",
        ]

        query_params: Dict[str, Any] = {
            "collections": [id],
            "fields": fields,
            "limit": limit,
            "max_items": max_items,
        }

        if spatial_extent is not None:
            bbox = [
                spatial_extent.west,
                spatial_extent.south,
                spatial_extent.east,
                spatial_extent.north,
            ]

            crs = pyproj.crs.CRS(spatial_extent.crs or "epsg:4326")
            if not crs.equals("EPSG:4326"):
                trans = pyproj.Transformer.from_crs(
                    crs,
                    pyproj.CRS.from_epsg(4326),
                    always_xy=True,
                )
                bbox = trans.transform_bounds(*bbox, densify_pts=21)

            query_params["bbox"] = bbox

        if temporal_extent is not None:
            start_date = None
            end_date = None
            if temporal_extent.start is not None:
                start_date = str(temporal_extent.start.to_numpy())
            if temporal_extent.end is not None:
                end_date = str(temporal_extent.end.to_numpy())

            if not end_date and not start_date:
                raise TemporalExtentEmpty()

            # Create datetime string in the format "start_date/end_date"
            if start_date and end_date:
                query_params["datetime"] = f"{start_date}/{end_date}"
            elif start_date:
                query_params["datetime"] = f"{start_date}/.."
            elif end_date:
                query_params["datetime"] = f"../{end_date}"

        if properties is not None:
            # Convert OpenEO process graphs to STAC CQL2-JSON format
            filter_expr = self._convert_process_graph_to_cql2(
                properties, named_parameters
            )
            query_params["filter"] = filter_expr
            query_params["filter_lang"] = "cql2-json"

        # The single funnel every `load_collection` read passes through, which
        # is why the deployment's signing decision is applied here and nowhere
        # below: from this point on an item carries what the read path needs
        # (docs/adr/0005-asset-href-signing.md S2.2).
        return [
            stamp_signer_key(item, self.signer_key)
            for item in self.stac_api.get_items(**query_params)
        ]

    def _handle_comparison_operator(
        self,
        process_id: str,
        prop_name: str,
        args: dict,
        named_parameters: Optional[dict] = None,
    ) -> Optional[Dict]:
        """Handle comparison operators like eq, lt, gt, etc."""
        operators = {
            "eq": "=",
            "neq": "<>",
            "lt": "<",
            "lte": "<=",
            "gt": ">",
            "gte": ">=",
        }

        if process_id in operators:
            # Resolve parameter reference in the 'y' argument if present
            y_value = args.get("y")
            is_ref, resolved_value = self._resolve_parameter_reference(
                y_value, named_parameters
            )
            if is_ref:
                if resolved_value is None:
                    # Parameter not found, skip this filter
                    return None
                y_value = resolved_value

            return {
                "op": operators[process_id],
                "args": [{"property": f"{prop_name}"}, y_value],
            }

        if process_id == "between":
            # Resolve parameter references in min/max arguments if present
            min_value = args.get("min")
            max_value = args.get("max")

            is_ref, resolved_value = self._resolve_parameter_reference(
                min_value, named_parameters
            )
            if is_ref:
                if resolved_value is None:
                    return None
                min_value = resolved_value

            is_ref, resolved_value = self._resolve_parameter_reference(
                max_value, named_parameters
            )
            if is_ref:
                if resolved_value is None:
                    return None
                max_value = resolved_value

            return {
                "op": "between",
                "args": [
                    {"property": f"{prop_name}"},
                    min_value,
                    max_value,
                ],
            }

        return None

    def _handle_array_operator(
        self,
        process_id: str,
        prop_name: str,
        args: dict,
        named_parameters: Optional[dict] = None,
    ) -> Optional[Dict]:
        """Handle array operators like in, array_contains."""
        if process_id in ["in", "array_contains"]:
            # Resolve parameter reference in the 'values' argument if present
            values = args.get("values", [])
            is_ref, resolved_value = self._resolve_parameter_reference(
                values, named_parameters
            )
            if is_ref:
                if resolved_value is None:
                    return None
                values = resolved_value

            return {
                "op": "in",
                "args": [
                    {"property": f"{prop_name}"},
                    {"array": values},
                ],
            }
        return None

    def _handle_pattern_operator(
        self,
        process_id: str,
        prop_name: str,
        args: dict,
        named_parameters: Optional[dict] = None,
    ) -> Optional[Dict]:
        """Handle pattern matching operators like starts_with, ends_with, contains."""

        def resolve_pattern_value(value):
            """Resolve parameter reference for pattern value."""
            is_ref, resolved_value = self._resolve_parameter_reference(
                value, named_parameters
            )
            if is_ref:
                return resolved_value if resolved_value is not None else ""
            return value or ""

        if process_id == "starts_with":
            # Support both 'y' (OpenEO standard) and 'pattern' argument names
            pattern_arg = args.get("y") or args.get("pattern")
            pattern = resolve_pattern_value(pattern_arg) + "%"
            return {
                "op": "like",
                "args": [{"property": f"{prop_name}"}, pattern],
            }
        elif process_id == "ends_with":
            # Support both 'y' (OpenEO standard) and 'pattern' argument names
            pattern_arg = args.get("y") or args.get("pattern")
            pattern = "%" + resolve_pattern_value(pattern_arg)
            return {
                "op": "like",
                "args": [{"property": f"{prop_name}"}, pattern],
            }
        elif process_id == "contains":
            # Support both 'y' (OpenEO standard) and 'pattern' argument names
            pattern_arg = args.get("y") or args.get("pattern")
            pattern = "%" + resolve_pattern_value(pattern_arg) + "%"
            return {
                "op": "like",
                "args": [{"property": f"{prop_name}"}, pattern],
            }
        return None

    def _handle_null_check(self, process_id: str, prop_name: str) -> Optional[Dict]:
        """Handle null check operators."""
        if process_id == "is_null":
            return {
                "op": "is null",
                "args": [{"property": f"{prop_name}"}],
            }
        return None

    def _handle_logical_operator(
        self,
        process_id: str,
        prop_name: str,
        args: dict,
        node_id: str,
        named_parameters: Optional[dict] = None,
    ) -> Optional[Dict]:
        """Handle logical operators like and, or, not."""
        if process_id not in ["and", "or", "not"]:
            return None

        if process_id in ["and", "or"]:
            sub_conditions = []
            for sub_arg in args.get("expressions", []):
                # Recursively process sub-conditions
                sub_pg = {"process_graph": {f"sub_{node_id}": sub_arg}}
                sub_condition = self._convert_process_graph_to_cql2(
                    {prop_name: sub_pg}, named_parameters
                )
                if sub_condition:
                    sub_conditions.append(sub_condition)

            if sub_conditions:
                return {"op": process_id, "args": sub_conditions}

        elif process_id == "not":
            # Recursively process the negated condition
            sub_pg = {"process_graph": {f"sub_{node_id}": args.get("expression")}}
            sub_condition = self._convert_process_graph_to_cql2(
                {prop_name: sub_pg}, named_parameters
            )
            if sub_condition:
                return {"op": "not", "args": [sub_condition]}

        return None

    def _handle_default_operator(
        self, prop_name: str, args: dict, named_parameters: Optional[dict] = None
    ) -> Optional[Dict]:
        """Handle default case for unmatched processes."""
        # Try to extract target value from arguments
        target_value = None
        for _, arg_value in args.items():
            # Check if it's a reference to the special "value" parameter
            is_ref, _ = self._resolve_parameter_reference(arg_value, {})
            if is_ref:
                # Check if it's the special "value" parameter
                param_name = None
                if isinstance(arg_value, ParameterReference):
                    param_name = arg_value.from_parameter
                elif isinstance(arg_value, dict):
                    param_name = arg_value.get("from_parameter")

                if param_name == "value":
                    continue

                # Try to resolve the parameter
                is_ref, resolved_value = self._resolve_parameter_reference(
                    arg_value, named_parameters
                )
                if is_ref and resolved_value is not None:
                    target_value = resolved_value
                    break
            else:
                target_value = arg_value
                break

        if target_value is not None:
            return {
                "op": "=",
                "args": [{"property": f"properties.{prop_name}"}, target_value],
            }
        return None

    def _handle_direct_value(self, prop_name: str, value) -> Dict:
        """Handle non-process graph case (direct value)."""
        return {"op": "=", "args": [{"property": f"{prop_name}"}, value]}

    def _process_single_property(
        self, prop_name: str, process_graph, named_parameters: Optional[dict] = None
    ) -> Optional[Dict]:
        """Process a single property in the process graph."""
        # Handle non-process graph case (direct value)
        if not isinstance(process_graph, dict) or "process_graph" not in process_graph:
            return self._handle_direct_value(prop_name, process_graph)

        # Extract process graph
        pg = process_graph["process_graph"]
        if not pg:
            return None

        # Get the node ID and node
        node_id = next(iter(pg.keys()))
        node = pg[node_id]
        process_id = node.get("process_id")
        args = node.get("arguments", {})

        # Try each operator type handler
        handlers = [
            lambda p_id, p_name, a: self._handle_comparison_operator(
                p_id, p_name, a, named_parameters
            ),
            lambda p_id, p_name, a: self._handle_array_operator(
                p_id, p_name, a, named_parameters
            ),
            lambda p_id, p_name, a: self._handle_pattern_operator(
                p_id, p_name, a, named_parameters
            ),
            lambda p_id, p_name, a: self._handle_null_check(p_id, p_name),
            lambda p_id, p_name, a: self._handle_logical_operator(
                p_id, p_name, a, node_id, named_parameters
            ),
        ]

        for handler in handlers:
            # Call handler without checking process_id type first
            condition = handler(process_id, prop_name, args)  # type: ignore
            if condition:
                return condition

        # Try default handler as fallback
        return self._handle_default_operator(prop_name, args, named_parameters)

    def _convert_process_graph_to_cql2(
        self, properties: dict, named_parameters: Optional[dict] = None
    ) -> dict:
        """
        Convert OpenEO process graph properties to STAC CQL2-JSON format.

        Args:
            properties: Dictionary of property name to OpenEO process graph
            named_parameters: Dictionary of parameter values for resolving parameter references

        Returns:
            Dictionary in CQL2-JSON format following the STAC API Filter Extension spec
        """
        if not properties:
            return {}

        # For single property we return the condition directly
        if len(properties) == 1:
            prop_name = next(iter(properties.keys()))
            condition = self._process_single_property(
                prop_name, properties[prop_name], named_parameters
            )
            return condition if condition else {}

        # For multiple properties, combine with AND
        cql2_filter: Dict[str, Any] = {"op": "and", "args": []}
        for prop_name, process_graph in properties.items():
            condition = self._process_single_property(
                prop_name, process_graph, named_parameters
            )
            if condition:
                cql2_filter["args"].append(condition)

        return cql2_filter

    def load_collection(
        self,
        id: str,
        spatial_extent: Optional[Union[BoundingBox, Dict[str, Any]]] = None,
        temporal_extent: Optional[TemporalInterval] = None,
        bands: Optional[list[str]] = None,
        properties: Optional[dict] = None,
        # private arguments
        width: Optional[int] = 1024,
        height: Optional[int] = 1024,
        tile_buffer: Optional[float] = None,
        named_parameters: Optional[dict] = None,
        target_crs: Optional[Union[int, str]] = None,
    ) -> RasterStack:
        """Load Collection.

        Args:
            id: Collection ID
            spatial_extent: Bounding box for the output (coordinates in its own
                CRS), or GeoJSON polygons: the data is loaded for their bounding
                box and the pixels outside the polygons are set to no data.
            temporal_extent: Temporal filter
            bands: Band names to load
            properties: Metadata filters
            width: Output width in pixels
            height: Output height in pixels
            tile_buffer: Tile overlap buffer
            named_parameters: Named parameters for process graph evaluation
            target_crs: Target CRS for output. If None, uses native CRS from source images.
        """
        polygons: Optional[List[Dict[str, Any]]] = None
        if spatial_extent is not None and not isinstance(spatial_extent, BoundingBox):
            spatial_extent, polygons = _geojson_spatial_extent(
                spatial_extent, "load_collection"
            )

        # Retrieve up to one item beyond the configured processing limit so the
        # guard below can detect genuine overflow. Without an explicit max_items
        # the STAC search silently caps at the first page (100, newest-first),
        # which drops whole months/years from wide temporal extents and yields
        # empty aggregate_temporal buckets (all-no-data). See issue #300.
        items = self._get_items(
            id,
            spatial_extent=spatial_extent,
            temporal_extent=temporal_extent,
            properties=properties,
            max_items=processing_settings.max_items + 1,
            named_parameters=named_parameters,
        )
        if not items:
            raise NoDataAvailable("There is no data available for the given extents.")

        # Check the items limit. Fail loudly when the extent genuinely contains
        # more items than allowed rather than silently truncating the result.
        if len(items) > processing_settings.max_items:
            raise ItemsLimitExceeded(len(items), processing_settings.max_items)

        # Check pixel limit before calling _estimate_output_dimensions
        # For test_load_collection_pixel_threshold
        if width and height:
            width_int = int(width)
            height_int = int(height)
            pixel_count = width_int * height_int * len(items)
            if pixel_count > processing_settings.max_pixels:
                raise OutputLimitExceeded(
                    width_int,
                    height_int,
                    processing_settings.max_pixels,
                    items_count=len(items),
                )

        # If bands parameter is missing, use the first asset from the first item
        if bands is None and items and items[0].assets:
            bands = list(items[0].assets.keys())[:1]  # Take the first asset as default

        # Estimate dimensions based on items and spatial extent
        dimensions = _estimate_output_dimensions(
            items,
            spatial_extent,
            bands,
            width,
            height,
            target_crs=target_crs,
        )

        # Extract values from the result
        width = dimensions["width"]
        height = dimensions["height"]
        bbox = dimensions["bbox"]
        bounds_crs = dimensions["bounds_crs"]
        output_crs = dimensions["crs"]

        # Reproject bbox from bounds_crs to output_crs for the RasterStack bounds
        # This ensures the output GeoTIFF has coordinates in the correct CRS
        if bounds_crs != output_crs:
            output_bbox = list(
                transform_bounds(bounds_crs, output_crs, *bbox, densify_pts=21)
            )
        else:
            output_bbox = bbox

        # Group items by date
        items_by_date: dict[str, list[Item]] = {}
        for item in items:
            date = item.datetime.isoformat()
            if date not in items_by_date:
                items_by_date[date] = []
            items_by_date[date].append(item)

        # Create lazy tasks for each date group
        # Each task will call mosaic_reader when executed
        def make_mosaic_task(
            date_items: list[Item],
            bbox: List[float],
            bounds_crs: Any,
            output_crs: Any,
            bands: Optional[list[str]],
            width: int,
            height: int,
            tile_buffer: Optional[float],
            polygons: Optional[List[Dict[str, Any]]],
        ):
            """Create a closure that loads data for a date group."""

            def task():
                # Build kwargs for mosaic_reader
                mosaic_kwargs = {
                    "threads": 0,
                    "bounds_crs": bounds_crs,
                    "assets": bands,
                    "dst_crs": output_crs,
                    "width": int(width) if width else width,
                    "height": int(height) if height else height,
                    "buffer": float(tile_buffer)
                    if tile_buffer is not None
                    else tile_buffer,
                    # Not just a stylistic default: FirstMethod picks its per-pixel
                    # winner from masks alone, per band independently, and derived
                    # (band-source) bands have their mask forced to match their
                    # sibling raw band's (reader.py's mask-inheritance post-step,
                    # ADR 0002 §2.4). So within one item's contribution, DN and its
                    # calibration/noise LUT bands always share a winner here --
                    # sar_backscatter (ADR 0002 §2.6) relies on this to never mix
                    # DN from one source item with LUT values from another. A
                    # value-driven method (Highest/Lowest) would not preserve that.
                    "pixel_selection": PixelSelectionMethod["first"].value(),
                }

                img, _ = mosaic_reader(
                    date_items,
                    _reader,
                    bbox,
                    **mosaic_kwargs,
                )
                if polygons:
                    # GeoJSON spatial extent: no data outside the polygons.
                    img = _mask_outside_polygons(img, polygons)
                return img

            return task

        # Build tasks list for RasterStack
        tasks = []
        for date, date_items in items_by_date.items():
            task_fn = make_mosaic_task(
                date_items,
                bbox,
                bounds_crs,
                output_crs,
                bands,
                width,
                height,
                tile_buffer,
                polygons,
            )
            # Collect all geometries from items for cutline mask computation (union of footprints)
            geometries = [
                item.geometry for item in date_items if item.geometry is not None
            ]
            tasks.append(
                (
                    task_fn,
                    {
                        "id": date,
                        "datetime": date_items[0].datetime if date_items else None,
                        "geometry": geometries if geometries else None,
                        # The source items behind this date group's mosaic. Carried
                        # so processes that need per-item STAC metadata (asset
                        # hrefs, properties) can reach it -- notably
                        # `sar_backscatter`, whose calibration LUTs and GCP
                        # geometry are per source item. Retrieve via
                        # `RasterStack.get_source_items`, never by reaching into
                        # task metadata directly. These are `pystac.Item`s.
                        "items": date_items,
                    },
                )
            )

        return RasterStack(
            tasks=tasks,
            timestamp_fn=lambda asset: asset["datetime"],
            width=int(width) if width else None,
            height=int(height) if height else None,
            bounds=output_bbox,
            dst_crs=output_crs,
            band_names=bands if bands else [],
        )


@define
class LoadStac:
    """Backend Specific STAC loaders."""

    #: See `LoadCollection.signer_key`. `load_stac` reads a user-supplied
    #: catalogue rather than the configured one, so the stamp does not by itself
    #: widen what gets signed: the signer it names still returns any href it has
    #: nothing to add to untouched, so a URL the user passed cannot pull a
    #: credential onto a host this deployment never meant to reach.
    signer_key: Optional[str] = field(default=None)

    def _load_stac_object(self, url: str) -> pystac.STACObject:
        """Load a STAC object from a URL.

        Args:
            url: URL to a static STAC catalog or a STAC API Collection

        Returns:
            The loaded STAC object

        Raises:
            ValueError: If the STAC object cannot be loaded
        """
        try:
            return pystac.read_file(url)
        except Exception as e:
            raise STACLoadError(url=url, error=str(e)) from e

    def _handle_collection_or_catalog(
        self,
        stac_obj: Union[pystac.Collection, pystac.Catalog],
        spatial_extent: Optional[Union[BoundingBox, Dict[str, Any]]] = None,
        temporal_extent: Optional[TemporalInterval] = None,
        bands: Optional[list[str]] = None,
        properties: Optional[dict] = None,
        width: Optional[int] = None,
        height: Optional[int] = None,
        tile_buffer: Optional[float] = None,
        named_parameters: Optional[dict] = None,
    ) -> RasterStack:
        """Handle a STAC Collection or Catalog.

        Args:
            stac_obj: The STAC Collection or Catalog
            spatial_extent: Optional bounding box to limit the data
            temporal_extent: Optional temporal interval to limit the data
            bands: Optional list of band names to include
            properties: Optional metadata properties to filter by
            width: Optional width of the output image in pixels
            height: Optional height of the output image in pixels
            tile_buffer: Optional buffer around the tile in pixels
            named_parameters: Named parameters for process graph evaluation

        Returns:
            A RasterStack containing the loaded data
        """
        collection_id = stac_obj.id
        stac_api = stacApiBackend(url=stac_obj.get_root_link().href)
        # The delegate inherits this instance's signer: handing off to a
        # LoadCollection for a user-supplied catalogue must not silently drop
        # the credential path.
        load_collection = LoadCollection(stac_api=stac_api, signer_key=self.signer_key)

        return load_collection.load_collection(
            id=collection_id,
            spatial_extent=spatial_extent,
            temporal_extent=temporal_extent,
            bands=bands,
            properties=properties,
            width=width,
            height=height,
            tile_buffer=tile_buffer,
            named_parameters=named_parameters,
        )

    def _filter_by_temporal_extent(
        self, items: List[Dict], temporal_extent: TemporalInterval
    ) -> List[Dict]:
        """Filter items by temporal extent.

        Args:
            items: List of STAC items
            temporal_extent: Temporal interval to filter by

        Returns:
            Filtered list of items

        Raises:
            TemporalExtentEmpty: If the temporal extent is empty
        """
        start_date = None
        end_date = None
        if temporal_extent[0] is not None:
            start_date = str(temporal_extent[0].to_numpy())
        if temporal_extent[1] is not None:
            end_date = str(temporal_extent[1].to_numpy())

        if not end_date and not start_date:
            raise TemporalExtentEmpty()

        filtered_items = []
        for item in items:
            item_datetime = item.get("properties", {}).get("datetime")
            if not item_datetime:
                continue

            if start_date and end_date:
                if start_date <= item_datetime < end_date:
                    filtered_items.append(item)
            elif start_date:
                if start_date <= item_datetime:
                    filtered_items.append(item)
            elif end_date:
                if item_datetime < end_date:
                    filtered_items.append(item)

        return filtered_items

    def _filter_by_properties(self, items: List[Dict], properties: dict) -> List[Dict]:
        """Filter items by properties.

        Args:
            items: List of STAC items
            properties: Properties to filter by

        Returns:
            Filtered list of items
        """
        filtered_items = []
        for item in items:
            include = True
            for prop_name, prop_value in properties.items():
                if (
                    prop_name not in item.get("properties", {})
                    or item["properties"][prop_name] != prop_value
                ):
                    include = False
                    break
            if include:
                filtered_items.append(item)
        return filtered_items

    def _process_spatial_extent(
        self,
        items: List[Dict],
        spatial_extent: BoundingBox,
        bands: Optional[list[str]] = None,
        width: Optional[int] = None,
        height: Optional[int] = None,
        tile_buffer: Optional[float] = None,
    ) -> RasterStack:
        """Process spatial extent and create tasks.

        Args:
            items: List of STAC items, already stamped with this deployment's
                signer key by the caller
            spatial_extent: Bounding box to limit the data
            bands: Optional list of band names to include
            width: Optional width of the output image in pixels
            height: Optional height of the output image in pixels
            tile_buffer: Optional buffer around the tile in pixels

        Returns:
            A RasterStack containing the tasks

        Raises:
            NotImplementedError: If spatial_extent is not provided
        """
        bbox = [
            spatial_extent.west,
            spatial_extent.south,
            spatial_extent.east,
            spatial_extent.north,
        ]
        projcrs = pyproj.crs.CRS(spatial_extent.crs or "epsg:4326")
        crs = to_rasterio_crs(projcrs)

        tasks = create_tasks(
            _reader,
            items,
            MAX_THREADS,
            bbox,
            assets=bands,
            bounds_crs=crs,
            dst_crs=crs,
            width=int(width) if width else width,
            height=int(height) if height else height,
            buffer=float(tile_buffer) if tile_buffer is not None else tile_buffer,
        )

        return RasterStack(
            tasks=tasks,
            timestamp_fn=lambda asset: _props_to_datetime(asset["properties"]),
            allowed_exceptions=(TileOutsideBounds,),
            # New parameters for truly lazy behavior
            width=int(width) if width else None,
            height=int(height) if height else None,
            bounds=tuple(bbox),
            dst_crs=crs,
            band_names=bands,
        )

    def load_stac(
        self,
        url: str,
        spatial_extent: Optional[Union[BoundingBox, Dict[str, Any]]] = None,
        temporal_extent: Optional[TemporalInterval] = None,
        bands: Optional[list[str]] = None,
        properties: Optional[dict] = None,
        # private arguments
        width: Optional[int] = None,
        height: Optional[int] = None,
        tile_buffer: Optional[float] = None,
        named_parameters: Optional[dict] = None,
    ) -> RasterStack:
        """Load data from a STAC catalog or API.

        Args:
            url: URL to a static STAC catalog or a STAC API Collection
            spatial_extent: Optional bounding box to limit the data, or GeoJSON
                polygons: the data is loaded for their bounding box and the
                pixels outside the polygons are set to no data.
            temporal_extent: Optional temporal interval to limit the data
            bands: Optional list of band names to include
            properties: Optional metadata properties to filter by
            width: Optional width of the output image in pixels
            height: Optional height of the output image in pixels
            tile_buffer: Optional buffer around the tile in pixels
            named_parameters: Named parameters for process graph evaluation

        Returns:
            A RasterStack containing the loaded data

        Raises:
            NoDataAvailable: If no data is available for the given extents
            TemporalExtentEmpty: If the temporal extent is empty
            NotImplementedError: If spatial_extent is not provided
        """
        # Validate a GeoJSON extent before any read. A Collection or Catalog
        # gets the GeoJSON itself: load_collection applies it.
        bbox_extent: Optional[BoundingBox] = None
        polygons: Optional[List[Dict[str, Any]]] = None
        if isinstance(spatial_extent, BoundingBox):
            bbox_extent = spatial_extent
        elif spatial_extent is not None:
            bbox_extent, polygons = _geojson_spatial_extent(spatial_extent, "load_stac")

        # Load the STAC catalog or item from the URL
        stac_obj = self._load_stac_object(url)

        # If the STAC object is a Collection or Catalog, use load_collection
        if isinstance(stac_obj, (pystac.Collection, pystac.Catalog)):
            return self._handle_collection_or_catalog(
                stac_obj,
                spatial_extent=spatial_extent,
                temporal_extent=temporal_extent,
                bands=bands,
                properties=properties,
                width=width,
                height=height,
                tile_buffer=tile_buffer,
                named_parameters=named_parameters,
            )

        # For a single item, use it directly. Stamped here because this path
        # never passes through `LoadCollection._get_items`, which is where every
        # other read gets its stamp.
        elif isinstance(stac_obj, pystac.Item):
            items = [stamp_signer_key(stac_obj.to_dict(), self.signer_key)]
        else:
            raise UnsupportedSTACObject(str(type(stac_obj)))

        if not items:
            raise NoDataAvailable("There is no data available in the STAC catalog.")

        # Filter items by temporal extent if provided
        if temporal_extent is not None:
            items = self._filter_by_temporal_extent(items, temporal_extent)

        # Filter items by properties if provided
        if properties is not None:
            items = self._filter_by_properties(items, properties)

        if not items:
            raise NoDataAvailable("There is no data available for the given extents.")

        # Process spatial extent
        if bbox_extent:
            stack = self._process_spatial_extent(
                items,
                bbox_extent,
                bands=bands,
                width=width,
                height=height,
                tile_buffer=tile_buffer,
            )
            if polygons:
                # GeoJSON spatial extent: no data outside the polygons, lazily.
                return stack.map_tasks(
                    lambda key, realize: _mask_outside_polygons(realize(), polygons)
                )
            return stack

        # Estimate dimensions based on items and spatial extent
        dimensions = _estimate_output_dimensions(
            items, bbox_extent, bands, width, height
        )

        # Extract values from the result
        width = dimensions["width"]
        height = dimensions["height"]
        bbox = dimensions["bbox"]
        bounds_crs = dimensions["bounds_crs"]
        output_crs = dimensions["crs"]

        img, _ = mosaic_reader(
            items,
            _reader,
            bbox,
            bounds_crs=bounds_crs,
            dst_crs=output_crs,
            width=int(width) if width else width,
            height=int(height) if height else height,
            buffer=float(tile_buffer) if tile_buffer is not None else tile_buffer,
        )
        return img
