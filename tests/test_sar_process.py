"""Tests for titiler.openeo.processes.implementations.sar (sar_backscatter).

Issue #348 / ADR 0002 increment 6: `sar_backscatter` no longer resolves
assets or fetches XML itself -- it reads the calibration constant (`A`) and
noise value (`eta`) as ordinary bands already present on its input cube,
normally injected by the reader-requirement planner
(`titiler.openeo.reader_requirements`). Most tests here build a `RasterStack`
whose slice already carries those bands as plain constant arrays -- exercising
`sar_backscatter`'s own band-lookup + `calibration.calibrate()` logic in
isolation, the same way `tests/test_sar_calibration.py` isolates the
arithmetic itself. Real band-source reading (the values actually being
correct against the annotation XML) is covered by
`tests/test_calibration_band_reader.py` / `tests/test_band_source_readers.py`;
`test_multi_item_mosaic_calibrates_per_item_without_cross_mixing` below is the
one exception that goes through the real read path end to end, needed to
prove per-item mosaic correctness rather than just per-band arithmetic.
"""

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pystac
import pytest
import rasterio
from openeo_pg_parser_networkx.graph import OpenEOProcessGraph
from rasterio.control import GroundControlPoint
from rasterio.crs import CRS
from rio_tiler.models import ImageData
from rio_tiler.mosaic.methods import PixelSelectionMethod
from rio_tiler.mosaic.reader import mosaic_reader
from shapely.geometry import box, mapping

from titiler.openeo.errors import (
    DigitalElevationModelInvalid,
    FeatureUnsupported,
    ProcessParameterInvalid,
)
from titiler.openeo.processes import process_registry
from titiler.openeo.processes.implementations.data_model import RasterStack
from titiler.openeo.processes.implementations.reduce import apply_pixel_selection
from titiler.openeo.processes.implementations.sar import (
    _COEFFICIENT_BAND_SUFFIX,
    _sar_backscatter_requirement,
    sar_backscatter,
)
from titiler.openeo.reader import SimpleSTACReader, _inherit_derived_band_masks
from titiler.openeo.reader_requirements import resolve_requirements
from titiler.openeo.sar import annotation

FIXTURES = Path(__file__).parent / "fixtures" / "sar"
_CALIBRATION_XML = (FIXTURES / "calibration_ipf290.xml").read_bytes()
_NOISE_XML = (FIXTURES / "noise_ipf290.xml").read_bytes()
_CALIBRATION_LEGACY_XML = (FIXTURES / "calibration_legacy.xml").read_bytes()
_NOISE_LEGACY_XML = (FIXTURES / "noise_legacy.xml").read_bytes()

_MOSAIC_W = _MOSAIC_H = 8


def _date_group_meta(items: List[Any]) -> Dict[str, Any]:
    """Task metadata in the exact shape `load_collection` builds.

    Mirrors `titiler.openeo.stacapi.LoadCollection.load_collection`: one task per
    acquisition datetime, whose source items (possibly several, mosaicked) are
    carried under "items". Kept in one place so the contract with stacapi is
    obvious if either side changes.
    """
    return {
        "id": items[0].datetime.isoformat() if items else "empty",
        "datetime": items[0].datetime if items else None,
        "geometry": None,
        "items": items,
    }


def _make_stack(
    dn: Dict[str, np.ndarray],
    *,
    width: int,
    height: int,
    array_mask: Optional[np.ndarray] = None,
    item_overrides: Optional[Dict[str, Any]] = None,
    extra_bands: Optional[Dict[str, np.ndarray]] = None,
) -> RasterStack:
    """A single-item, single-timestamp RasterStack wired for sar_backscatter tests.

    `dn`: {polarisation: (height, width) array}. `extra_bands`: {band_name:
    (height, width) array} for any `{pol}_<suffix>` band a test needs present
    on the cube -- sar_backscatter reads these directly from the array by
    name, so tests supply them directly as plain constants rather than
    through the real annotation/GCP read path (covered elsewhere -- see the
    module docstring). No assets are attached to the item: sar_backscatter no
    longer resolves any, and the product-type gate only ever reads
    `item.properties`.
    """
    polarisations = list(dn)
    extra_bands = extra_bands or {}
    band_names = polarisations + list(extra_bands)

    properties: Dict[str, Any] = {
        "datetime": "2024-01-01T00:00:00Z",
        "sar:product_type": "GRD",
    }
    if item_overrides and "properties" in item_overrides:
        properties = item_overrides["properties"]

    # A real pystac.Item, because that is what load_collection carries.
    item = pystac.Item(
        id="S1TEST_0001",
        geometry=None,
        bbox=None,
        datetime=datetime(2024, 1, 1),
        properties=properties,
    )

    stacked = np.stack(
        [dn[name] for name in polarisations]
        + [extra_bands[name] for name in extra_bands]
    ).astype("float32")
    array = np.ma.MaskedArray(
        stacked, mask=array_mask if array_mask is not None else False
    )
    image = ImageData(
        array,
        crs=CRS.from_epsg(4326),
        bounds=(0, 0, 1, 1),
        band_names=band_names,
        band_descriptions=band_names,
    )

    def task_fn() -> ImageData:
        return image

    return RasterStack(
        tasks=[(task_fn, _date_group_meta([item]))],
        timestamp_fn=lambda asset: asset["datetime"],
        width=width,
        height=height,
        bounds=(0.0, 0.0, 1.0, 1.0),
        dst_crs=CRS.from_epsg(4326),
        band_names=band_names,
    )


def _only_image(stack: RasterStack) -> ImageData:
    key = next(iter(stack.keys()))
    return stack[key]


# --------------------------------------------------------------------------- parameter validation


@pytest.mark.parametrize("coefficient", ["sigma0-terrain", "gamma0-terrain"])
def test_rejects_terrain_coefficients(coefficient):
    stack = _make_stack({"vv": np.array([[100]])}, width=1, height=1)
    with pytest.raises(ProcessParameterInvalid, match="terrain-corrected"):
        sar_backscatter(stack, coefficient=coefficient)


def test_rejects_unsupported_coefficient():
    """Not a terrain coefficient (rejected separately, above), just not a
    real one -- calibrate() used to be the only place this raised; that check
    moved to _validate_parameters when calibrate() dropped its coefficient
    parameter entirely (ADR 0002 increment 6)."""
    stack = _make_stack({"vv": np.array([[100]])}, width=1, height=1)
    with pytest.raises(ProcessParameterInvalid, match="not a supported"):
        sar_backscatter(stack, coefficient="not-a-real-coefficient")


def test_rejects_contributing_area():
    stack = _make_stack({"vv": np.array([[100]])}, width=1, height=1)
    with pytest.raises(FeatureUnsupported):
        sar_backscatter(stack, coefficient="sigma0-ellipsoid", contributing_area=True)


def test_rejects_local_incidence_angle():
    stack = _make_stack({"vv": np.array([[100]])}, width=1, height=1)
    with pytest.raises(FeatureUnsupported):
        sar_backscatter(
            stack, coefficient="sigma0-ellipsoid", local_incidence_angle=True
        )


def test_rejects_elevation_model():
    stack = _make_stack({"vv": np.array([[100]])}, width=1, height=1)
    with pytest.raises(DigitalElevationModelInvalid):
        sar_backscatter(
            stack, coefficient="sigma0-ellipsoid", elevation_model="COPERNICUS_30"
        )


def test_rejects_stack_without_band_names():
    stack = RasterStack(
        tasks=[(lambda: ImageData(np.ma.array(np.ones((1, 1, 1)))), {"id": "x"})],
        timestamp_fn=lambda asset: datetime(2024, 1, 1),
    )
    with pytest.raises(ProcessParameterInvalid, match="band names"):
        sar_backscatter(stack, coefficient="sigma0-ellipsoid")


def test_rejects_non_item_backed_stack():
    """RasterStack.from_images carries no STAC item metadata for its tasks."""
    stack = RasterStack.from_images(
        {datetime(2024, 1, 1): ImageData(np.ma.array(np.ones((1, 2, 2))))},
        band_names=["vv"],
    )
    with pytest.raises(ProcessParameterInvalid, match="no source-item metadata"):
        sar_backscatter(stack, coefficient="sigma0-ellipsoid")


def test_rejects_slc_product_type():
    stack = _make_stack(
        {"vv": np.array([[100]])},
        width=1,
        height=1,
        item_overrides={
            "properties": {
                "datetime": "2024-01-01T00:00:00Z",
                "sar:product_type": "SLC",
            }
        },
    )
    with pytest.raises(ProcessParameterInvalid, match="SLC"):
        sar_backscatter(stack, coefficient="sigma0-ellipsoid")


def test_accepts_item_with_no_product_type_metadata():
    """Absent product-type metadata must not be gated on (capability, not identity)."""
    stack = _make_stack(
        {"vv": np.array([[100]])},
        width=1,
        height=1,
        item_overrides={"properties": {"datetime": "2024-01-01T00:00:00Z"}},
        extra_bands={"vv_sigma0_lut": np.array([[2.0]])},
    )
    result = sar_backscatter(stack, coefficient="sigma0-ellipsoid", noise_removal=False)
    assert _only_image(result).count == 1


# --------------------------------------------------------------------------- band lookup


def test_rejects_stack_with_only_derived_bands_no_polarisation():
    """A cube carrying only a derived band, no raw polarisation, is not
    distinguishable from "no polarisations requested" -- same error as
    test_rejects_stack_without_band_names, now exercising the polarisation
    filter (_POLARISATION_CODES) rather than an empty band list."""
    stack = _make_stack(
        {}, width=1, height=1, extra_bands={"vv_sigma0_lut": np.array([[2.0]])}
    )
    with pytest.raises(ProcessParameterInvalid, match="band names"):
        sar_backscatter(stack, coefficient="sigma0-ellipsoid", noise_removal=False)


def test_rejects_missing_calibration_band():
    """coefficient requires a sigma0_lut band; it is not present -- this is
    normally injected by the planner and its absence should be a clear
    error, not a KeyError. `map_tasks` is lazy, so the error only surfaces
    once the slice is actually realized, not at the `sar_backscatter()` call
    itself."""
    stack = _make_stack({"vv": np.array([[100]])}, width=1, height=1)
    result = sar_backscatter(stack, coefficient="sigma0-ellipsoid", noise_removal=False)
    with pytest.raises(ProcessParameterInvalid, match="vv_sigma0_lut"):
        _only_image(result)


def test_rejects_missing_noise_band():
    """noise_removal=True (the default) requires a noise_lut band."""
    stack = _make_stack(
        {"vv": np.array([[100]])},
        width=1,
        height=1,
        extra_bands={"vv_sigma0_lut": np.array([[2.0]])},
    )
    result = sar_backscatter(stack, coefficient="sigma0-ellipsoid")
    with pytest.raises(ProcessParameterInvalid, match="vv_noise_lut"):
        _only_image(result)


# --------------------------------------------------------------------------- band assembly


def test_band_assembly_names_dtype_and_shape():
    stack = _make_stack(
        {"vv": np.array([[500]])},
        width=1,
        height=1,
        extra_bands={
            "vv_sigma0_lut": np.array([[2.0]]),
            "vv_noise_lut": np.array([[10.0]]),
            "vv_ellipsoid_incidence_angle": np.array([[35.0]]),
        },
    )
    result = sar_backscatter(
        stack,
        coefficient="sigma0-ellipsoid",
        mask=True,
        ellipsoid_incidence_angle=True,
    )
    img = _only_image(result)
    assert img.band_descriptions == ["vv", "ellipsoid_incidence_angle", "mask"]
    assert img.array.dtype == np.float32
    assert img.array.shape == (3, 1, 1)


def test_band_assembly_without_optional_bands():
    stack = _make_stack(
        {"vv": np.array([[500]]), "vh": np.array([[300]])},
        width=1,
        height=1,
        extra_bands={
            "vv_sigma0_lut": np.array([[2.0]]),
            "vh_sigma0_lut": np.array([[2.0]]),
        },
    )
    result = sar_backscatter(stack, coefficient="sigma0-ellipsoid", noise_removal=False)
    img = _only_image(result)
    assert img.band_descriptions == ["vv", "vh"]
    assert img.array.shape == (2, 1, 1)


# --------------------------------------------------------------------------- radiometry


def test_null_coefficient_returns_uncalibrated_power():
    stack = _make_stack({"vv": np.array([[100]])}, width=1, height=1)
    result = sar_backscatter(stack, coefficient=None, noise_removal=False)
    value = _only_image(result).array.data[0, 0, 0]
    assert value == pytest.approx(100.0**2, rel=1e-5)


def test_noise_removal_differs_measurably_at_low_backscatter():
    """ADR acceptance criterion 5: noise_removal true/false differ measurably
    where noise dominates (low DN)."""
    stack_on = _make_stack(
        {"vv": np.array([[10]])},
        width=1,
        height=1,
        extra_bands={
            "vv_sigma0_lut": np.array([[2.0]]),
            "vv_noise_lut": np.array([[1200.0]]),
        },
    )
    stack_off = _make_stack(
        {"vv": np.array([[10]])},
        width=1,
        height=1,
        extra_bands={"vv_sigma0_lut": np.array([[2.0]])},
    )

    with_noise = sar_backscatter(
        stack_on, coefficient="sigma0-ellipsoid", noise_removal=True
    )
    without_noise = sar_backscatter(
        stack_off, coefficient="sigma0-ellipsoid", noise_removal=False
    )

    on_value = _only_image(with_noise).array.data[0, 0, 0]
    off_value = _only_image(without_noise).array.data[0, 0, 0]
    assert on_value == 0.0  # clamped: noise (1200) swamps dn**2 (100)
    assert off_value > 0.0


def test_noise_removal_agrees_at_high_backscatter():
    """...and agree closely where the signal dwarfs the noise floor (high DN)."""
    stack_on = _make_stack(
        {"vv": np.array([[2000]])},
        width=1,
        height=1,
        extra_bands={
            "vv_sigma0_lut": np.array([[2.0]]),
            "vv_noise_lut": np.array([[1200.0]]),
        },
    )
    stack_off = _make_stack(
        {"vv": np.array([[2000]])},
        width=1,
        height=1,
        extra_bands={"vv_sigma0_lut": np.array([[2.0]])},
    )

    with_noise = sar_backscatter(
        stack_on, coefficient="sigma0-ellipsoid", noise_removal=True
    )
    without_noise = sar_backscatter(
        stack_off, coefficient="sigma0-ellipsoid", noise_removal=False
    )

    on_value = _only_image(with_noise).array.data[0, 0, 0]
    off_value = _only_image(without_noise).array.data[0, 0, 0]
    assert on_value == pytest.approx(off_value, rel=1e-2)


def test_mask_band_reflects_dn_zero_border():
    stack = _make_stack(
        {"vv": np.array([[0, 500]])},
        width=2,
        height=1,
        extra_bands={"vv_sigma0_lut": np.array([[2.0, 2.0]])},
    )
    result = sar_backscatter(
        stack, coefficient="sigma0-ellipsoid", mask=True, noise_removal=False
    )
    img = _only_image(result)
    mask_band = img.array.data[img.band_descriptions.index("mask")]
    assert mask_band[0, 0] == 0.0  # DN == 0 -> invalid/no-data
    assert mask_band[0, 1] == 1.0


def test_combines_stack_mask_with_dn_validity():
    """A pixel the *stack* already marked invalid must stay invalid even if
    its DN happens to be > 0 (ADR S7.3 step 7: preserve the stack's own mask)."""
    # (band, height, width) -- vv's own mask, then vv_sigma0_lut's (all valid).
    array_mask = np.array([[[True, False]], [[False, False]]])
    stack = _make_stack(
        {"vv": np.array([[500, 500]])},
        width=2,
        height=1,
        array_mask=array_mask,
        extra_bands={"vv_sigma0_lut": np.array([[2.0, 2.0]])},
    )
    result = sar_backscatter(
        stack, coefficient="sigma0-ellipsoid", mask=True, noise_removal=False
    )
    img = _only_image(result)
    mask_band = img.array.data[img.band_descriptions.index("mask")]
    assert mask_band[0, 0] == 0.0  # masked upstream despite dn > 0
    assert mask_band[0, 1] == 1.0
    assert bool(img.array.mask[0, 0, 0]) is True
    assert bool(img.array.mask[0, 0, 1]) is False


def test_ellipsoid_incidence_angle_band_is_plausible_degrees():
    stack = _make_stack(
        {"vv": np.array([[500]])},
        width=1,
        height=1,
        extra_bands={
            "vv_sigma0_lut": np.array([[2.0]]),
            "vv_ellipsoid_incidence_angle": np.array([[35.0]]),
        },
    )
    result = sar_backscatter(
        stack,
        coefficient="sigma0-ellipsoid",
        ellipsoid_incidence_angle=True,
        noise_removal=False,
    )
    img = _only_image(result)
    angle = img.array.data[img.band_descriptions.index("ellipsoid_incidence_angle")]
    assert 0.0 < float(angle[0, 0]) < 90.0


# --------------------------------------------------------------------------- integration


def test_sar_backscatter_is_registered():
    assert "sar_backscatter" in process_registry
    assert process_registry["sar_backscatter"].spec["id"] == "sar_backscatter"


def test_sar_backscatter_runs_through_the_real_process_graph():
    """Discoverable and callable as an actual graph node, not just a plain
    Python function -- the real executor path (see
    test_apply_graph_integration.py for the pattern)."""
    stack = _make_stack(
        {"vv": np.array([[500]])},
        width=1,
        height=1,
        extra_bands={"vv_sigma0_lut": np.array([[2.0]])},
    )
    pg = {
        "sar": {
            "process_id": "sar_backscatter",
            "arguments": {
                "data": {"from_parameter": "data"},
                "coefficient": "sigma0-ellipsoid",
                "noise_removal": False,
            },
            "result": True,
        }
    }
    callable_ = OpenEOProcessGraph(pg_data={"process_graph": pg}).to_callable(
        process_registry=process_registry
    )
    result = callable_(named_parameters={"data": stack})
    assert _only_image(result).band_descriptions == ["vv"]


# --------------------------------------------------------------------------- reader-requirement provider


def test_requirement_provider_needs_lut_and_noise_bands_per_polarisation():
    requirement = _sar_backscatter_requirement(
        {"coefficient": "sigma0-ellipsoid", "noise_removal": True},
        {"id": "sentinel-1-grd", "bands": ["vv", "vh"]},
    )
    assert requirement.extra_bands == frozenset(
        {"vv_sigma0_lut", "vh_sigma0_lut", "vv_noise_lut", "vh_noise_lut"}
    )


def test_requirement_provider_honours_ellipsoid_incidence_angle_flag():
    requirement = _sar_backscatter_requirement(
        {
            "coefficient": None,
            "noise_removal": False,
            "ellipsoid_incidence_angle": True,
        },
        {"id": "sentinel-1-grd", "bands": ["vv"]},
    )
    assert requirement.extra_bands == frozenset({"vv_ellipsoid_incidence_angle"})


def test_requirement_provider_requests_nothing_for_null_coefficient_no_extras():
    requirement = _sar_backscatter_requirement(
        {"coefficient": None, "noise_removal": False},
        {"id": "sentinel-1-grd", "bands": ["vv"]},
    )
    assert requirement.extra_bands == frozenset()


def test_requirement_provider_ignores_a_non_polarisation_band_name():
    """The ancestor load_collection's own bands can carry non-polarisation
    names too (e.g. the user separately requested a derived band for their
    own purposes) -- only real polarisation codes drive this provider."""
    requirement = _sar_backscatter_requirement(
        {"coefficient": "beta0", "noise_removal": False},
        {"id": "sentinel-1-grd", "bands": ["vv", "vv_dn_lut"]},
    )
    assert requirement.extra_bands == frozenset({"vv_beta0_lut"})


def test_requirement_provider_does_not_crash_on_unresolved_coefficient():
    """A UDP from_parameter reference is still unresolved at plan time (the
    same class of value reader_requirements._signature_key already guards
    id/bands against, ADR 0002 §3.1/increment 4) -- must degrade to no LUT
    band, not raise."""
    unresolved = object()
    requirement = _sar_backscatter_requirement(
        {"coefficient": unresolved, "noise_removal": True},
        {"id": "sentinel-1-grd", "bands": ["vv"]},
    )
    assert requirement.extra_bands == frozenset({"vv_noise_lut"})


def test_resolve_requirements_does_not_crash_for_a_parameterized_coefficient():
    """End to end through the real planner: a graph whose sar_backscatter
    node's coefficient comes from a UDP parameter must not crash
    resolve_requirements, even though it can't be resolved at plan time."""
    pg = {
        "load": {
            "process_id": "load_collection",
            "arguments": {"id": "sentinel-1-grd", "bands": ["vv", "vh"]},
        },
        "sar": {
            "process_id": "sar_backscatter",
            "arguments": {
                "data": {"from_node": "load"},
                "coefficient": {"from_parameter": "coef"},
            },
            "result": True,
        },
    }
    graph = OpenEOProcessGraph(pg_data=pg)
    resolve_requirements(graph)  # must not raise


def test_coefficient_band_suffix_keys_match_coefficient_lut():
    """These two tables describe the same three coefficients under different
    spellings (ADR 0002 increment 6) -- must never drift apart."""
    assert set(_COEFFICIENT_BAND_SUFFIX) == set(annotation.COEFFICIENT_LUT)


# --------------------------------------------------------------------------- mosaic across slices


def _half_covering_slice(item_id: str, day: int, cols: slice, footprint):
    """One slice covering only part of the AOI, in load_collection's real shape.

    Faithful to what the backend actually builds: DN is no-data outside the
    item's footprint, and the task metadata carries the footprint under
    "geometry" as a *list* (as load_collection does), which is what gives the
    ImageRef a real cutline mask.
    """
    item = pystac.Item(
        id=item_id,
        geometry=mapping(footprint),
        bbox=list(footprint.bounds),
        datetime=datetime(2024, 1, day),
        properties={"sar:product_type": "GRD"},
    )

    dn = np.zeros((1, _MOSAIC_H, _MOSAIC_W), dtype="float32")
    dn[:, :, cols] = 2000  # bright, well above the noise floor
    sigma0_lut = np.full((1, _MOSAIC_H, _MOSAIC_W), 2.0, dtype="float32")
    nodata = np.ones((2, _MOSAIC_H, _MOSAIC_W), dtype=bool)
    nodata[:, :, cols] = False
    image = ImageData(
        np.ma.MaskedArray(np.concatenate([dn, sigma0_lut]), mask=nodata),
        crs=CRS.from_epsg(4326),
        bounds=(0, 0, 1, 1),
        band_names=["vv", "vv_sigma0_lut"],
        band_descriptions=["vv", "vv_sigma0_lut"],
    )
    meta = {
        "id": item.datetime.isoformat(),
        "datetime": item.datetime,
        "geometry": [mapping(footprint)],
        "items": [item],
    }
    return (lambda: image, meta)


def _two_half_slices_stack() -> RasterStack:
    """A 2-slice stack whose slices together cover the AOI (left half, right half)."""
    return RasterStack(
        tasks=[
            _half_covering_slice(
                "LEFT", 1, slice(0, _MOSAIC_W // 2), box(0, 0, 0.5, 1)
            ),
            _half_covering_slice(
                "RIGHT", 2, slice(_MOSAIC_W // 2, _MOSAIC_W), box(0.5, 0, 1, 1)
            ),
        ],
        timestamp_fn=lambda asset: asset["datetime"],
        width=_MOSAIC_W,
        height=_MOSAIC_H,
        bounds=(0.0, 0.0, 1.0, 1.0),
        dst_crs=CRS.from_epsg(4326),
        band_names=["vv", "vv_sigma0_lut"],
    )


@pytest.mark.parametrize("with_mask_band", [False, True])
def test_calibrated_slices_still_mosaic_together(with_mask_band):
    """Calibrated slices must still stitch: map_tasks preserves what mosaicking needs.

    Two slices each covering half the AOI, run through sar_backscatter and then
    apply_pixel_selection("first"), must produce a fully-filled image. This
    guards the map_tasks rewrite against dropping the per-slice masks/geometry
    that pixel selection depends on. Parametrized over the `mask` band because
    it adds a band whose mask semantics differ from the data bands.
    """
    calibrated = sar_backscatter(
        _two_half_slices_stack(),
        coefficient="sigma0-ellipsoid",
        mask=with_mask_band,
        noise_removal=False,
    )
    mosaicked = apply_pixel_selection(calibrated, pixel_selection="first")
    img = _only_image(mosaicked)

    vv = img.array[img.band_descriptions.index("vv")]
    assert not np.ma.getmaskarray(vv).any(), (
        "mosaic left unfilled pixels; per-column masked counts: "
        f"{np.ma.getmaskarray(vv).sum(axis=0).tolist()}"
    )
    assert (vv.data > 0).all(), (
        "mosaic has zero-valued pixels; per-column zero counts: "
        f"{(vv.data == 0).sum(axis=0).tolist()}"
    )


def test_mask_band_does_not_report_nodata_as_valid():
    """The `mask` band must not make a slice's no-data region look like data.

    `ImageData._mask` is `logical_or.reduce(~array.mask)` -- a pixel is valid if
    ANY band is unmasked -- so an unmasked informational `mask` band would make
    `img.mask` (and hence GeoTIFF nodata/alpha on save_result) claim the whole
    grid is data, including the half this slice does not cover.
    """
    calibrated = sar_backscatter(
        _two_half_slices_stack(),
        coefficient="sigma0-ellipsoid",
        mask=True,
        noise_removal=False,
    )
    first_key = next(iter(calibrated.keys()))
    img = calibrated[first_key]

    valid_px = int((img.mask > 0).sum())
    half = _MOSAIC_W * _MOSAIC_H // 2
    assert valid_px == half, (
        f"dataset mask reports {valid_px}/{_MOSAIC_W * _MOSAIC_H} px valid; "
        f"this slice covers only {half}"
    )


# --------------------------------------------------------------------------- real multi-item mosaic


class _FixtureFetcher:
    """A fake AssetFetcher serving fixed bytes by href, with a call log."""

    def __init__(self, mapping: Dict[str, bytes]):
        self._mapping = mapping
        self.calls: List[str] = []

    def fetch(self, href: str) -> bytes:
        """Return the fixed payload for href, recording the call."""
        self.calls.append(href)
        return self._mapping[href]


def _write_measurement_gcp_tiff(path: Path) -> None:
    """A minimal GCP-referenced GeoTIFF -- only its header/GCPs matter here
    (`geocode.get_gcps` never reads pixel data). See
    tests/test_band_source_readers.py for why 16 points, not 4: `OpenEOReader`
    fits an order-3 polynomial (`MAX_GCP_ORDER=3`, needs >= 10 points)."""
    rows = np.linspace(0, 8000, 4)
    cols = np.linspace(0, 12000, 4)
    gcps = [
        GroundControlPoint(row=row, col=col, x=col / 12000, y=1 - row / 8000)
        for row in rows
        for col in cols
    ]
    with rasterio.open(
        path, "w", driver="GTiff", width=2, height=2, count=1, dtype="uint16"
    ) as dst:
        dst.write(np.full((2, 2), 100, dtype="uint16"), 1)
        dst.gcps = (gcps, CRS.from_epsg(4326))


def _real_derived_bands_reader(item, bbox, **kwargs):
    """Mirrors `titiler.openeo.reader._reader`'s core logic (real
    `SimpleSTACReader` read + mask inheritance) but, unlike `_reader`, forwards
    a fetcher -- `_reader` always uses the production default fetcher, with no
    override seam, so a test needing a fake one for local fixture bytes must
    reimplement that little.
    """
    fetcher = kwargs.pop("band_source_fetcher", None)
    with SimpleSTACReader(item, band_source_fetcher=fetcher) as src_dst:
        img = src_dst.part(bbox, **kwargs)
        requested = kwargs.get("assets")
        if requested:
            img = _inherit_derived_band_masks(
                img, getattr(src_dst, "_derived_bands", {}), requested
            )
    return img


def test_multi_item_mosaic_calibrates_per_item_without_cross_mixing(tmp_path):
    """The claim that makes lifting sar.py's old multi-item-per-slice
    rejection safe: stacapi.py hardcodes pixel_selection="first" for the
    intra-datetime mosaic, which picks its per-pixel winner from masks alone,
    per band independently, and derived LUT bands have their mask forced to
    match their sibling raw band's (reader.py's mask-inheritance post-step).
    So within one item's contribution, DN and its calibration/noise bands
    are always selected from the SAME winning item at every pixel -- never a
    cross-item mix.

    Proven through the real read path: real `CalibrationBandReader`/
    `NoiseBandReader`, real `mosaic_reader` + `PixelSelectionMethod["first"]`
    (the same machinery stacapi.py's load_collection uses), and two real,
    numerically distinct fixture annotation files (ipf290 vs legacy schema
    generation). Each item's coverage is forced to a known, non-overlapping
    half of the grid rather than left to chance GCP-warp edge behavior: this
    fixture's tiny scale makes the *measurement* DN read itself degenerate
    (all-masked -- a known, GDAL-version-sensitive property of a 2x2-pixel
    source raster, unrelated to what this test is about), so DN is supplied
    directly and only the calibration/noise LUT bands go through the real
    per-item read. The oracle is computed independently, directly against the
    real annotation LUTs at the same (line, pixel) coordinates the real read
    used (recovered via the same `geocode.build_inverse_map` call, since both
    items share identical GCPs).
    """
    width, height = 2, 1
    bbox = (0.0, 0.0, 1.0, 1.0)

    measurement_path_a = tmp_path / "measurement_a.tif"
    measurement_path_b = tmp_path / "measurement_b.tif"
    _write_measurement_gcp_tiff(measurement_path_a)
    _write_measurement_gcp_tiff(measurement_path_b)

    cal_href_a, noise_href_a = "fixture://mosaic-cal-a", "fixture://mosaic-noise-a"
    cal_href_b, noise_href_b = "fixture://mosaic-cal-b", "fixture://mosaic-noise-b"
    fetcher = _FixtureFetcher(
        {
            cal_href_a: _CALIBRATION_XML,
            noise_href_a: _NOISE_XML,
            cal_href_b: _CALIBRATION_LEGACY_XML,
            noise_href_b: _NOISE_LEGACY_XML,
        }
    )

    def _item(
        item_id: str, measurement_path: Path, cal_href: str, noise_href: str
    ) -> pystac.Item:
        item = pystac.Item(
            id=item_id,
            geometry={
                "type": "Polygon",
                "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]],
            },
            bbox=[0, 0, 1, 1],
            datetime=datetime(2024, 1, 1),
            properties={"sar:product_type": "GRD"},
            collection="sentinel-1-grd",
        )
        item.add_asset(
            "vv",
            pystac.Asset(
                href=str(measurement_path),
                media_type="image/tiff; application=geotiff; profile=cloud-optimized",
                roles=["data"],
            ),
        )
        item.add_asset(
            "schema-calibration-vv",
            pystac.Asset(
                href=cal_href, media_type="application/xml", roles=["metadata"]
            ),
        )
        item.add_asset(
            "schema-noise-vv",
            pystac.Asset(
                href=noise_href, media_type="application/xml", roles=["metadata"]
            ),
        )
        return item

    item_a = _item("item-a", measurement_path_a, cal_href_a, noise_href_a)
    item_b = _item("item-b", measurement_path_b, cal_href_b, noise_href_b)

    # item-a wins column 0, item-b wins column 1 -- forced (see docstring).
    dn_value = {"item-a": 500.0, "item-b": 700.0}
    forced_mask = {
        "item-a": np.array([[False, True]]),
        "item-b": np.array([[True, False]]),
    }

    def combined_reader(item, bbox, **kwargs):
        derived_kwargs = dict(kwargs, assets=["vv_sigma0_lut", "vv_noise_lut"])
        derived = _real_derived_bands_reader(item, bbox, **derived_kwargs)
        dn_band = np.full((1, height, width), dn_value[item.id], dtype="float32")
        combined = np.concatenate([dn_band, derived.array.data], axis=0)
        mask = np.broadcast_to(forced_mask[item.id], combined.shape)
        array = np.ma.MaskedArray(combined, mask=mask.copy())
        return ImageData(
            array,
            crs=derived.crs,
            bounds=derived.bounds,
            band_names=["vv", "vv_sigma0_lut", "vv_noise_lut"],
            band_descriptions=["vv", "vv_sigma0_lut", "vv_noise_lut"],
        )

    img, _ = mosaic_reader(
        [item_a, item_b],
        combined_reader,
        bbox,
        assets=["vv_sigma0_lut", "vv_noise_lut"],
        dst_crs=CRS.from_epsg(4326),
        bounds_crs=CRS.from_epsg(4326),
        width=width,
        height=height,
        pixel_selection=PixelSelectionMethod["first"].value(),
        band_source_fetcher=fetcher,
        threads=0,
    )

    meta = {
        "id": "2024-01-01",
        "datetime": datetime(2024, 1, 1),
        "items": [item_a, item_b],
    }
    stack = RasterStack(
        tasks=[(lambda: img, meta)],
        timestamp_fn=lambda asset: asset["datetime"],
        width=width,
        height=height,
        bounds=bbox,
        dst_crs=CRS.from_epsg(4326),
        band_names=["vv", "vv_sigma0_lut", "vv_noise_lut"],
    )

    result = sar_backscatter(stack, coefficient="sigma0-ellipsoid", noise_removal=True)
    calibrated = _only_image(result)
    assert not calibrated.array.mask.any()

    from titiler.openeo.sar import geocode

    gcps, gcp_crs = geocode.get_gcps(str(measurement_path_a))
    inverse = geocode.build_inverse_map(
        gcps, gcp_crs, width, height, bbox, CRS.from_epsg(4326)
    )

    oracles = {
        "item-a": (
            annotation.parse_calibration(_CALIBRATION_XML),
            annotation.parse_noise(_NOISE_XML),
        ),
        "item-b": (
            annotation.parse_calibration(_CALIBRATION_LEGACY_XML),
            annotation.parse_noise(_NOISE_LEGACY_XML),
        ),
    }
    for col, item_id in enumerate(["item-a", "item-b"]):
        cal, noise = oracles[item_id]
        a = cal.sigma_nought(inverse.line, inverse.pixel)[0, col]
        eta = noise.evaluate(inverse.line, inverse.pixel)[0, col]
        power = max(dn_value[item_id] ** 2 - eta, 0.0)
        expected = power / a**2
        actual = float(calibrated.array.data[0, 0, col])
        assert actual == pytest.approx(expected, rel=1e-6), (
            f"column {col} (won by {item_id}) does not match its own oracle -- "
            "possible cross-item DN/LUT mixing"
        )
