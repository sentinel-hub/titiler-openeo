"""titiler.openeo.processes sar_backscatter (Phase 1: ellipsoid coefficients).

Ties `titiler.openeo.sar`'s building blocks together into the openEO
`sar_backscatter` process: resolve each item's calibration/noise/measurement
assets, geocode, calibrate, and assemble the calibrated `ImageData`. Reading
itself stays `RasterStack`'s job -- see `sar/geocode.py` and
docs/adr/0001-sar-backscatter.md S7.1-S7.4.
"""

import re
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


def _asset_href(asset: Dict[str, Any]) -> str:
    """Resolve an asset's href, preferring the STAC_ALTERNATE_KEY variant.

    Mirrors SimpleSTACReader's alternate-href handling (../../reader.py).
    """
    if STAC_ALTERNATE_KEY:
        alternate = asset.get("alternate", {}).get(STAC_ALTERNATE_KEY)
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


def _resolve_polarisation_assets(
    item: Dict[str, Any], polarisation: str
) -> Tuple[str, str, str]:
    """Resolve (measurement, calibration, noise) hrefs for one polarisation."""
    item_id = item.get("id", "<unknown>")
    assets = item.get("assets", {})
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


def _check_product_type(item: Dict[str, Any]) -> None:
    """Reject items positively known to be unsupported; accept everything else."""
    sar_type = item.get("properties", {}).get("sar:product_type")
    if sar_type and sar_type.upper() in _UNSUPPORTED_PRODUCT_TYPES:
        raise ProcessParameterInvalid(
            f"Item {item.get('id', '<unknown>')!r} has sar:product_type "
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
) -> Dict[str, Dict[str, Tuple[str, str, str]]]:
    """Resolve every item's per-polarisation asset hrefs, eagerly.

    Metadata-only (dict lookups via `get_stac_item`), no pixel I/O -- so a bad
    graph fails immediately rather than only once some downstream node
    consumes a slice.
    """
    resolved: Dict[str, Dict[str, Tuple[str, str, str]]] = {}
    for key in data.keys():
        item = data.get_stac_item(key)
        if not isinstance(item, dict) or "assets" not in item:
            raise ProcessParameterInvalid(
                "sar_backscatter requires `data` to be a STAC-item-backed "
                "load_collection stack (no asset metadata found for one of "
                "its timestamps)"
            )
        item_id = item.get("id", "<unknown>")
        if item_id in resolved:
            continue
        _check_product_type(item)
        resolved[item_id] = {
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

    def transform(item: Dict[str, Any], realize: Callable[[], ImageData]) -> ImageData:
        img = realize()
        # Always set by the read path (SimpleSTACReader/OpenEOReader) that produced
        # `img`; asserted rather than typed non-optional since ImageData's own
        # fields are Optional for the general case.
        assert img.bounds is not None and img.crs is not None
        hrefs = resolved[item.get("id", "<unknown>")]
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
            # openEO's own contract for this band (not rio-tiler's internal
            # 0/255 mask convention): 1.0 valid, 0.0 invalid/no-data. The mask
            # band's own values are never themselves "no data".
            band_values.append(
                np.where(combined_invalid, np.float32(0.0), np.float32(1.0))
            )
            band_masks.append(np.zeros_like(combined_invalid))

        array = np.ma.MaskedArray(np.stack(band_values), mask=np.stack(band_masks))
        return ImageData(
            array,
            crs=img.crs,
            bounds=img.bounds,
            band_names=new_band_names,
            band_descriptions=new_band_names,
        )

    return data.map_tasks(transform, band_names=new_band_names)
