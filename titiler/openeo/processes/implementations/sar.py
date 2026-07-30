"""titiler.openeo.processes sar_backscatter (Phase 1: ellipsoid coefficients).

Ties `titiler.openeo.sar`'s building blocks together into the openEO
`sar_backscatter` process: resolve each item's calibration/noise/measurement
assets, geocode, calibrate, and assemble the calibrated `ImageData`. Reading
itself stays `RasterStack`'s job -- see `sar/geocode.py` and
docs/adr/0001-sar-backscatter.md S7.1-S7.4.
"""

import re
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np
from rio_tiler.io.stac import STAC_ALTERNATE_KEY
from rio_tiler.models import ImageData

from ...errors import (
    DigitalElevationModelInvalid,
    FeatureUnsupported,
    ProcessParameterInvalid,
)
from ...sar import annotation, calibration, geocode
from ...sar.fetcher import AssetFetcher
from .data_model import RasterStack

__all__ = ["sar_backscatter"]

#: Rejected explicitly per ADR S7.4 -- Phase 1 is ellipsoid-only.
_UNSUPPORTED_COEFFICIENTS = {"sigma0-terrain", "gamma0-terrain"}

#: Positively known to be unsupported (detected-amplitude assumption breaks
#: down); absence or a GRD-ish value is accepted -- gate on capability
#: (asset resolution), not identity (issue #340 "open design detail").
_UNSUPPORTED_PRODUCT_TYPES = {"SLC", "OCN"}


def _item_id(item: Any) -> str:
    """Item id, from a plain STAC dict or a `pystac.Item`."""
    if isinstance(item, dict):
        return str(item.get("id", "<unknown>"))
    return str(getattr(item, "id", "<unknown>"))


def _item_properties(item: Any) -> Dict[str, Any]:
    """Item properties, from a plain STAC dict or a `pystac.Item`."""
    if isinstance(item, dict):
        return item.get("properties", {}) or {}
    return getattr(item, "properties", {}) or {}


def _asset_fields(asset: Any) -> Dict[str, Any]:
    """Flatten one asset to a plain dict of fields, including `href`.

    `load_collection` hands processes `pystac.Item`s, whose assets are
    `pystac.Asset` objects (href as an attribute, everything else in
    `extra_fields`); `load_stac` hands through plain STAC dicts. Normalizing
    once here keeps the resolution logic below written against one shape.
    """
    if isinstance(asset, dict):
        return asset
    fields = dict(getattr(asset, "extra_fields", {}) or {})
    href = getattr(asset, "href", None)
    if href is not None:
        fields["href"] = href
    return fields


def _item_assets(item: Any) -> Dict[str, Dict[str, Any]]:
    """All of an item's assets, normalized to `{name: fields_dict}`."""
    raw = (
        item.get("assets", {})
        if isinstance(item, dict)
        else getattr(item, "assets", {})
    )
    return {name: _asset_fields(asset) for name, asset in (raw or {}).items()}


def _asset_href(asset: Dict[str, Any]) -> str:
    """Resolve an asset's href, preferring the STAC_ALTERNATE_KEY variant.

    Mirrors SimpleSTACReader's alternate-href handling (../../reader.py).
    Expects an already-normalized asset dict (see `_asset_fields`).
    """
    if STAC_ALTERNATE_KEY:
        alternate = (asset.get("alternate") or {}).get(STAC_ALTERNATE_KEY)
        if alternate and alternate.get("href"):
            return alternate["href"]
    return asset["href"]


def _find_annotation_asset(
    assets: Dict[str, Any], kind: str, polarisation: str, item_id: str
) -> Dict[str, Any]:
    """Resolve the calibration/noise sibling asset for one polarisation.

    `schema-calibration-<pol>`/`schema-noise-<pol>` is the convention across
    CDSE, Earth Search and Planetary Computer (confirmed against real items in
    increment 2), but is not an openEO or STAC guarantee -- fall back to
    matching the annotation filename convention instead (e.g.
    `.../calibration-...-hh-....xml`), which held on all three catalogues
    checked.
    """
    key = f"schema-{kind}-{polarisation}"
    if key in assets:
        return assets[key]

    pattern = re.compile(rf"/{kind}[-_][^/]*{re.escape(polarisation)}", re.IGNORECASE)
    matches = [
        asset
        for asset in assets.values()
        if isinstance(asset, dict) and "href" in asset and pattern.search(asset["href"])
    ]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise ProcessParameterInvalid(
            f"Item {item_id!r}: ambiguous {kind} asset for polarisation "
            f"{polarisation!r}: {[a['href'] for a in matches]}"
        )
    raise ProcessParameterInvalid(
        f"Item {item_id!r} is missing the {kind} annotation asset for "
        f"polarisation {polarisation!r} (expected 'schema-{kind}-{polarisation}' "
        f"or an asset href matching '{kind}[-_]...{polarisation}')"
    )


def _resolve_polarisation_assets(item: Any, polarisation: str) -> Tuple[str, str, str]:
    """Resolve (measurement, calibration, noise) hrefs for one polarisation."""
    item_id = _item_id(item)
    assets = _item_assets(item)
    if polarisation not in assets:
        raise ProcessParameterInvalid(
            f"Item {item_id!r} has no {polarisation!r} measurement asset "
            "(sar_backscatter needs one asset per requested polarisation, "
            "matching the band names passed to load_collection)"
        )
    measurement_href = _asset_href(assets[polarisation])
    calibration_href = _asset_href(
        _find_annotation_asset(assets, "calibration", polarisation, item_id)
    )
    noise_href = _asset_href(
        _find_annotation_asset(assets, "noise", polarisation, item_id)
    )
    return measurement_href, calibration_href, noise_href


def _check_product_type(item: Any) -> None:
    """Reject items positively known to be unsupported; accept everything else."""
    sar_type = _item_properties(item).get("sar:product_type")
    if sar_type and str(sar_type).upper() in _UNSUPPORTED_PRODUCT_TYPES:
        raise ProcessParameterInvalid(
            f"Item {_item_id(item)!r} has sar:product_type "
            f"{sar_type!r}; sar_backscatter expects detected GRD amplitude data"
        )


def _validate_parameters(
    coefficient: Optional[str],
    elevation_model: Optional[str],
    contributing_area: bool,
    local_incidence_angle: bool,
) -> None:
    """Raise for every Phase-1-unsupported parameter combination (ADR S7.4)."""
    if coefficient in _UNSUPPORTED_COEFFICIENTS:
        raise ProcessParameterInvalid(
            f"coefficient={coefficient!r}: terrain-corrected coefficients are "
            "not supported; use 'sigma0-ellipsoid' or 'gamma0-ellipsoid', or "
            "load a pre-computed RTC collection"
        )
    if contributing_area or local_incidence_angle:
        raise FeatureUnsupported(
            "contributing_area and local_incidence_angle require a DEM, which "
            "Phase 1 of sar_backscatter does not use"
        )
    if elevation_model is not None:
        raise DigitalElevationModelInvalid(
            f"elevation_model={elevation_model!r}: Phase 1 of sar_backscatter "
            "uses no DEM; only elevation_model=null (ellipsoid geometry) is "
            "supported"
        )


def _resolve_stack_assets(
    data: RasterStack, polarisations: List[str]
) -> Dict[datetime, Dict[str, Tuple[str, str, str]]]:
    """Resolve each slice's per-polarisation asset hrefs, eagerly, keyed by stack key.

    Metadata-only (`get_source_items` plus dict lookups), no pixel I/O -- so a
    bad graph fails immediately rather than only once some downstream node
    consumes a slice.

    Each slice must resolve to exactly one source item. `load_collection`
    mosaics all items sharing an acquisition datetime into a single image, and
    calibration is inherently per item -- every item carries its own LUTs and
    its own GCP geometry, so no single item's LUTs can correctly calibrate a
    blend of several. Rather than silently applying one item's radiometry to
    another's pixels, that case is rejected. Doing it properly means
    calibrating per item *before* the mosaic, i.e. in the read path (ADR
    S7.10(b)), which Phase 1 does not build.
    """
    resolved: Dict[datetime, Dict[str, Tuple[str, str, str]]] = {}
    for key in data.keys():
        items = data.get_source_items(key)
        if not items:
            raise ProcessParameterInvalid(
                "sar_backscatter requires `data` to come from load_collection "
                "or load_stac, which carry the STAC metadata it needs; the "
                f"slice at {key} has no source-item metadata (a stack built "
                "from in-memory images cannot be calibrated)"
            )
        if len(items) > 1:
            raise ProcessParameterInvalid(
                f"The slice at {key} mosaics {len(items)} source items "
                f"({', '.join(_item_id(i) for i in items)}). sar_backscatter "
                "calibrates per source item -- each has its own calibration "
                "LUTs and geolocation -- so it cannot calibrate an already "
                "mosaicked blend of several. Narrow the spatial or temporal "
                "extent so each acquisition datetime resolves to one item."
            )
        item = items[0]
        _check_product_type(item)
        resolved[key] = {
            pol: _resolve_polarisation_assets(item, pol) for pol in polarisations
        }
    return resolved


def sar_backscatter(
    data: RasterStack,
    coefficient: Optional[str] = "gamma0-terrain",
    elevation_model: Optional[str] = None,
    mask: bool = False,
    contributing_area: bool = False,
    local_incidence_angle: bool = False,
    ellipsoid_incidence_angle: bool = False,
    noise_removal: bool = True,
    options: Optional[Dict[str, Any]] = None,
) -> RasterStack:
    """Compute Sentinel-1 GRD radiometric backscatter (Phase 1: ellipsoid only).

    See docs/adr/0001-sar-backscatter.md for the full design. `coefficient`
    supports `beta0`, `sigma0-ellipsoid`, `gamma0-ellipsoid` and `null`; the
    spec default `gamma0-terrain` and `sigma0-terrain` are rejected -- callers
    must opt into an ellipsoid coefficient explicitly. `elevation_model` must
    be `null` (no DEM is used); `contributing_area` and `local_incidence_angle`
    are not supported.

    Args:
        data: The RasterStack from `load_collection` for an S1 GRD collection,
            with one band per requested polarisation (e.g. `bands=["vv","vh"]`).
        coefficient: Radiometric correction coefficient.
        elevation_model: Must be `None` in Phase 1.
        mask: If True, append a `mask` band (1.0 valid, 0.0 invalid/no-data).
        contributing_area: Not supported.
        local_incidence_angle: Not supported.
        ellipsoid_incidence_angle: If True, append an `ellipsoid_incidence_angle`
            band in degrees.
        noise_removal: If True (default), subtract ESA's thermal noise LUT.
        options: Back-end-specific options. Recognizes `fetcher` (an
            `AssetFetcher`) for the calibration/noise XML fetch, defaulting to
            `titiler.openeo.sar.fetcher.get_default_fetcher()` -- this is a
            test/deployment seam, not part of the openEO process contract.

    Returns:
        RasterStack: One band per requested polarisation, calibrated to linear
        backscatter, `float32`, plus any requested extra bands.
    """
    _validate_parameters(
        coefficient, elevation_model, contributing_area, local_incidence_angle
    )

    polarisations = list(data.band_names)
    if not polarisations:
        raise ProcessParameterInvalid(
            "sar_backscatter requires `data` to carry band names (one per "
            "requested polarisation), e.g. load_collection(..., bands=[...])"
        )

    fetcher: Optional[AssetFetcher] = (options or {}).get("fetcher")
    resolved = _resolve_stack_assets(data, polarisations)

    new_band_names = list(polarisations)
    if ellipsoid_incidence_angle:
        new_band_names.append("ellipsoid_incidence_angle")
    if mask:
        new_band_names.append("mask")

    def transform(key: datetime, realize: Callable[[], ImageData]) -> ImageData:
        img = realize()
        # Always set by the read path (SimpleSTACReader/OpenEOReader) that produced
        # `img`; asserted rather than typed non-optional since ImageData's own
        # fields are Optional for the general case.
        assert img.bounds is not None and img.crs is not None
        hrefs = resolved[key]
        # np.ma.getmaskarray, not img.array.mask directly: the latter can be the
        # scalar `nomask` sentinel (not indexable) when nothing upstream masked
        # any pixel; this always returns a full per-band boolean array.
        data_mask = np.ma.getmaskarray(img.array)

        band_values: List[np.ndarray] = []
        combined_invalid: Optional[np.ndarray] = None
        incidence_angle: Optional[np.ndarray] = None

        # Positional, not by band_descriptions: `create_tasks` reads exactly
        # `data.band_names` (== `polarisations`), in that order, into `img`'s bands.
        for idx, pol in enumerate(polarisations):
            measurement_href, calibration_href, noise_href = hrefs[pol]
            dn = img.array.data[idx]

            gcps, gcp_crs = geocode.get_gcps(measurement_href)
            inverse = geocode.build_inverse_map(
                gcps, gcp_crs, img.width, img.height, img.bounds, img.crs
            )

            calibration_lut = annotation.get_calibration(
                calibration_href, fetcher=fetcher
            )
            noise_lut = (
                annotation.get_noise(noise_href, fetcher=fetcher)
                if noise_removal
                else None
            )
            result = calibration.calibrate(
                dn, inverse, calibration_lut, coefficient, noise_lut
            )

            pol_invalid = ~result.valid_mask | data_mask[idx]
            combined_invalid = (
                pol_invalid
                if combined_invalid is None
                else combined_invalid | pol_invalid
            )
            band_values.append(result.value.astype("float32"))

            if ellipsoid_incidence_angle and incidence_angle is None:
                incidence_angle = calibration_lut.ellipsoid_incidence_angle(
                    inverse.line, inverse.pixel
                ).astype("float32")

        # Guaranteed set: the loop above runs at least once (polarisations is
        # non-empty, checked before the closure is ever built).
        assert combined_invalid is not None

        # Every data band shares the combined per-pixel validity: a pixel is
        # only "data" if every requested polarisation has data there.
        band_masks = [combined_invalid] * len(band_values)

        if ellipsoid_incidence_angle:
            assert incidence_angle is not None
            band_values.append(incidence_angle)
            band_masks.append(combined_invalid)

        if mask:
            # Values follow openEO's contract for this band -- 1.0 valid, 0.0
            # invalid/no-data -- and survive in `array.data` regardless of the
            # mask below, so reading the band still reports validity per spec.
            #
            # Its *mask* must match the data bands even though the band is
            # informational. `ImageData._mask` is
            # `logical_or.reduce(~array.mask)` -- a pixel counts as valid if ANY
            # band is unmasked -- so leaving this band unmasked would report a
            # slice's no-data region as valid data at the ImageData level, to
            # every dataset-mask consumer (`img.mask`, GeoTIFF nodata/alpha on
            # save_result). Pixel selection is unaffected either way: it feeds
            # `img.array`, whose per-band masks are correct regardless.
            band_values.append(
                np.where(combined_invalid, np.float32(0.0), np.float32(1.0))
            )
            band_masks.append(combined_invalid)

        array = np.ma.MaskedArray(np.stack(band_values), mask=np.stack(band_masks))
        return ImageData(
            array,
            # Preserved so downstream mosaicking sees the same footprint the
            # read produced; dropping it loses the per-item cutline.
            cutline_mask=img.cutline_mask,
            crs=img.crs,
            bounds=img.bounds,
            assets=img.assets,
            metadata=img.metadata,
            band_names=new_band_names,
            band_descriptions=new_band_names,
        )

    return data.map_tasks(transform, band_names=new_band_names)
