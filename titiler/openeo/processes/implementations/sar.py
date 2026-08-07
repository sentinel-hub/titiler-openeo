"""titiler.openeo.processes sar_backscatter (Phase 1: ellipsoid coefficients).

Reads the calibration constant and noise value as ordinary cube bands --
injected by the reader-requirement planner (`titiler.openeo.reader_requirements`,
ADR 0002 §2.6) or requested explicitly by a caller -- and reduces calibration to
arithmetic (`sar/calibration.py`). See docs/adr/0002-band-sources.md §2.6 for the
convergence this module is the client of, and docs/adr/0001-sar-backscatter.md
S7.1-S7.4 for the read path (SimpleSTACReader/OpenEOReader) that produced those
bands, out of scope here.
"""

from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

import numpy as np
from rio_tiler.models import ImageData

from ...errors import (
    DigitalElevationModelInvalid,
    FeatureUnsupported,
    ProcessParameterInvalid,
)
from ...reader_requirements import Requirement, register_requirement_provider
from ...sar import calibration
from .data_model import RasterStack

__all__ = ["sar_backscatter"]

#: Rejected explicitly per ADR S7.4 -- Phase 1 is ellipsoid-only.
_UNSUPPORTED_COEFFICIENTS = {"sigma0-terrain", "gamma0-terrain"}

#: Positively known to be unsupported (detected-amplitude assumption breaks
#: down); absence or a GRD-ish value is accepted -- gate on capability
#: (asset resolution), not identity (issue #340 "open design detail").
_UNSUPPORTED_PRODUCT_TYPES = {"SLC", "OCN"}

#: The four Sentinel-1 polarisation codes -- what distinguishes a real
#: measurement band from a derived one riding alongside it in the same cube
#: (issue #348 / ADR 0002 increment 6). Both `sar_backscatter`'s own runtime
#: and its requirement provider (below) use this to tell them apart.
_POLARISATION_CODES = frozenset({"vv", "vh", "hh", "hv"})

#: openEO `coefficient` -> derived-band-name suffix (`bandsources/sources.py`'s
#: `{pol}_<suffix>` templates). A third spelling of the same three
#: coefficients: `sar.annotation.COEFFICIENT_LUT` already maps them to a LUT
#: array name (e.g. `"sigmaNought"`) and `BandSource.bands`' `quantity` maps
#: to a `CalibrationLUT` method name (e.g. `"sigma_nought"`) -- neither of
#: those is the band name itself. Must stay in sync with `COEFFICIENT_LUT`'s
#: keys -- `test_sar_process.py` asserts the key sets match.
_COEFFICIENT_BAND_SUFFIX: Dict[str, str] = {
    "beta0": "beta0_lut",
    "sigma0-ellipsoid": "sigma0_lut",
    "gamma0-ellipsoid": "gamma0_lut",
}


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


def _check_product_type(item: Any) -> None:
    """Reject items positively known to be unsupported; accept everything else."""
    sar_type = _item_properties(item).get("sar:product_type")
    if sar_type and str(sar_type).upper() in _UNSUPPORTED_PRODUCT_TYPES:
        raise ProcessParameterInvalid(
            f"Item {_item_id(item)!r} has sar:product_type "
            f"{sar_type!r}; sar_backscatter expects detected GRD amplitude data"
        )


def _check_product_types(data: RasterStack) -> None:
    """Eagerly validate every source item's product type, for every slice.

    Metadata-only (`get_source_items` plus a property lookup), no pixel I/O --
    so a bad graph fails immediately rather than only once some downstream
    node consumes a slice, the fail-fast property the old asset-resolution
    pass used to provide alongside its own work. Runs over every item in a
    slice, not just one: multi-item slices are fine to calibrate now (LUT
    bands are computed and mosaicked per item before `sar_backscatter` ever
    sees them, same as DN), so a bad item hiding behind a good one in the
    same mosaic must still be caught.
    """
    for key in data.keys():
        items = data.get_source_items(key)
        if not items:
            raise ProcessParameterInvalid(
                "sar_backscatter requires `data` to come from load_collection "
                "or load_stac, which carry the STAC metadata it needs; the "
                f"slice at {key} has no source-item metadata (a stack built "
                "from in-memory images cannot be calibrated)"
            )
        for item in items:
            _check_product_type(item)


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
    if coefficient is not None and coefficient not in _COEFFICIENT_BAND_SUFFIX:
        raise ProcessParameterInvalid(
            f"coefficient={coefficient!r} is not a supported calibration "
            f"coefficient; expected one of {sorted(_COEFFICIENT_BAND_SUFFIX)} "
            "or null"
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


def _band(band_index: Dict[str, int], array_data: np.ndarray, name: str) -> np.ndarray:
    """Look up one band's values by name, or raise a clear, actionable error.

    Deliberately agnostic to *why* `name` is missing -- whether the
    reader-requirement planner should have injected it (ADR 0002 §2.6) and
    didn't, or a caller built the graph without going through the normal
    request path. `sar_backscatter` only cares that the band is present,
    however it got there (ADR 0002 §3: "one code path... serving both a user
    requesting vv_sigma0_lut and sar_backscatter internally").
    """
    idx = band_index.get(name)
    if idx is None:
        raise ProcessParameterInvalid(
            f"sar_backscatter needs the {name!r} band on its input cube but "
            "it is not present. This is normally injected automatically by "
            "the reader-requirement planner (docs/adr/0002-band-sources.md); "
            "if you are calling sar_backscatter on a stack that bypassed the "
            f"normal request path, request {name!r} explicitly via "
            "load_collection(bands=[...])."
        )
    return array_data[idx]


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

    See docs/adr/0001-sar-backscatter.md for the full design and
    docs/adr/0002-band-sources.md §2.6 for how this process gets the
    calibration constant and noise value it needs: as ordinary bands on
    `data`, not fetched here. `coefficient` supports `beta0`,
    `sigma0-ellipsoid`, `gamma0-ellipsoid` and `null`; the spec default
    `gamma0-terrain` and `sigma0-terrain` are rejected -- callers must opt
    into an ellipsoid coefficient explicitly. `elevation_model` must be
    `null` (no DEM is used); `contributing_area` and `local_incidence_angle`
    are not supported.

    Args:
        data: The RasterStack from `load_collection` for an S1 GRD collection,
            with one band per requested polarisation (e.g. `bands=["vv","vh"]`),
            plus whichever `{pol}_<suffix>_lut`/`{pol}_noise_lut` bands this
            call needs -- normally injected automatically (ADR 0002 §2.6).
        coefficient: Radiometric correction coefficient.
        elevation_model: Must be `None` in Phase 1.
        mask: If True, append a `mask` band (1.0 valid, 0.0 invalid/no-data).
        contributing_area: Not supported.
        local_incidence_angle: Not supported.
        ellipsoid_incidence_angle: If True, append an `ellipsoid_incidence_angle`
            band in degrees.
        noise_removal: If True (default), subtract ESA's thermal noise LUT.
        options: Back-end-specific options. Currently unused -- kept for
            spec compatibility and as a future seam.

    Returns:
        RasterStack: One band per requested polarisation, calibrated to linear
        backscatter, `float32`, plus any requested extra bands.
    """
    _validate_parameters(
        coefficient, elevation_model, contributing_area, local_incidence_angle
    )

    polarisations = [b for b in data.band_names if b in _POLARISATION_CODES]
    if not polarisations:
        raise ProcessParameterInvalid(
            "sar_backscatter requires `data` to carry band names (one per "
            "requested polarisation), e.g. load_collection(..., bands=[...])"
        )

    _check_product_types(data)

    # Positional: `create_tasks` reads exactly `data.band_names`, in that
    # order, into every slice's `img.array` -- built once here, not per
    # slice, since it depends only on the stack's declared band names.
    band_index = {name: idx for idx, name in enumerate(data.band_names)}
    suffix = _COEFFICIENT_BAND_SUFFIX.get(coefficient) if coefficient else None

    new_band_names = list(polarisations)
    if ellipsoid_incidence_angle:
        new_band_names.append("ellipsoid_incidence_angle")
    if mask:
        new_band_names.append("mask")

    def transform(key: datetime, realize: Callable[[], ImageData]) -> ImageData:
        img = realize()
        # Always set by the read path (SimpleSTACReader/OpenEOReader) that produced `img`.
        if img.bounds is None or img.crs is None:
            raise ProcessParameterInvalid(
                "sar_backscatter requires input slices to include bounds and CRS metadata"
            )
        # np.ma.getmaskarray, not img.array.mask directly: the latter can be the
        # scalar `nomask` sentinel (not indexable) when nothing upstream masked
        # any pixel; this always returns a full per-band boolean array.
        data_mask = np.ma.getmaskarray(img.array)

        band_values: List[np.ndarray] = []
        combined_invalid: Optional[np.ndarray] = None
        incidence_angle: Optional[np.ndarray] = None

        for pol in polarisations:
            idx = band_index[pol]
            dn = img.array.data[idx]
            a = _band(band_index, img.array.data, f"{pol}_{suffix}") if suffix else None
            eta = (
                _band(band_index, img.array.data, f"{pol}_noise_lut")
                if noise_removal
                else None
            )

            result = calibration.calibrate(dn, a=a, eta=eta)

            pol_invalid = ~result.valid_mask | data_mask[idx]
            combined_invalid = (
                pol_invalid
                if combined_invalid is None
                else combined_invalid | pol_invalid
            )
            band_values.append(result.value.astype("float32"))

            if ellipsoid_incidence_angle and incidence_angle is None:
                incidence_angle = _band(
                    band_index, img.array.data, f"{pol}_ellipsoid_incidence_angle"
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


def _sar_backscatter_requirement(
    resolved_kwargs: Dict[str, Any], load_collection_kwargs: Dict[str, Any]
) -> Requirement:
    """What a `sar_backscatter` node needs from its `load_collection` ancestor
    (ADR 0002 §2.6): the calibration LUT band for its `coefficient` (if any)
    and the noise LUT band (if `noise_removal`), one pair per polarisation
    already requested there. `sar_backscatter` itself only ever learns its
    own polarisations from the already-loaded cube (`data.band_names`) at
    call time, but this runs at *plan* time, before any load happens -- the
    ancestor's own `bands` argument is the only source available here.

    `coefficient`/`noise_removal`/`ellipsoid_incidence_angle` can each still
    be an unresolved UDP `from_parameter` reference at this point, not yet a
    plain str/bool -- the same class of value
    `reader_requirements._signature_key` already guards `id`/`bands`
    against. `coefficient` is guarded explicitly (a non-str would otherwise
    crash the dict lookup below); the two booleans are read permissively
    since a stray truthy non-bool only over-injects an unused band, which is
    harmless -- `sar_backscatter`'s own validation still catches a genuinely
    bad `coefficient` once it is actually resolved.
    """
    coefficient = resolved_kwargs.get("coefficient", "gamma0-terrain")
    suffix = (
        _COEFFICIENT_BAND_SUFFIX.get(coefficient)
        if isinstance(coefficient, str)
        else None
    )
    noise_removal = resolved_kwargs.get("noise_removal", True)
    incidence_angle = resolved_kwargs.get("ellipsoid_incidence_angle", False)

    bands = load_collection_kwargs.get("bands")
    if not isinstance(bands, list):
        bands = []
    polarisations = [
        b for b in bands if isinstance(b, str) and b in _POLARISATION_CODES
    ]

    extra = set()
    for pol in polarisations:
        if suffix:
            extra.add(f"{pol}_{suffix}")
        if noise_removal:
            extra.add(f"{pol}_noise_lut")
        if incidence_angle:
            extra.add(f"{pol}_ellipsoid_incidence_angle")

    return Requirement(extra_bands=frozenset(extra))


register_requirement_provider("sar_backscatter", _sar_backscatter_requirement)
