"""Tests for titiler.openeo.processes.implementations.sar (sar_backscatter).

Builds a single-item, single-timestamp RasterStack with *real* tasks (not
RasterStack.from_images, which drops item metadata) over a minimal
GCP-referenced measurement asset, and serves the real committed IPF>=2.90
fixture calibration/noise XML (tests/fixtures/sar/*_ipf290.xml) through a fake
AssetFetcher injected via `options={"fetcher": ...}` -- the one internal seam
`sar_backscatter` exposes for this, per its docstring.

The measurement GCPs are chosen so the 1x1/2x1 destination grids used below
land within the fixture LUTs' real coordinate domain (line 0-~16000, pixel
0-~23000); `get_gcps` only reads header/tags, never pixels, so the file's
actual size/content is otherwise irrelevant.
"""

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pystac
import pytest
import rasterio
from openeo_pg_parser_networkx.graph import OpenEOProcessGraph
from rasterio.control import GroundControlPoint
from rasterio.crs import CRS
from rio_tiler.models import ImageData

from titiler.openeo.errors import (
    DigitalElevationModelInvalid,
    FeatureUnsupported,
    ProcessParameterInvalid,
)
from titiler.openeo.processes import process_registry
from titiler.openeo.processes.implementations.data_model import RasterStack
from titiler.openeo.processes.implementations.sar import sar_backscatter

FIXTURES = Path(__file__).parent / "fixtures" / "sar"
_CALIBRATION_XML = (FIXTURES / "calibration_ipf290.xml").read_bytes()
_NOISE_XML = (FIXTURES / "noise_ipf290.xml").read_bytes()


class _FixtureFetcher:
    """A fake AssetFetcher serving fixed bytes by href, with a call log."""

    def __init__(self, mapping: Dict[str, bytes]):
        self._mapping = mapping
        self.calls: List[str] = []

    def fetch(self, href: str) -> bytes:
        """Return the fixed payload for href, recording the call."""
        self.calls.append(href)
        return self._mapping[href]


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


def _write_measurement_gcp_tiff(path: Path) -> None:
    """A minimal GCP-referenced GeoTIFF.

    get_gcps only reads its header/tags, never pixel data, so size/content are
    irrelevant -- only the GCPs matter, chosen so the (0,0,1,1)-bounds
    destination grids below map into the fixture LUTs' real domain.
    """
    gcps = [
        GroundControlPoint(row=0, col=0, x=0, y=1),
        GroundControlPoint(row=0, col=12000, x=1, y=1),
        GroundControlPoint(row=8000, col=0, x=0, y=0),
        GroundControlPoint(row=8000, col=12000, x=1, y=0),
    ]
    with rasterio.open(
        path, "w", driver="GTiff", width=2, height=2, count=1, dtype="uint16"
    ) as dst:
        dst.write(np.zeros((2, 2), dtype="uint16"), 1)
        dst.gcps = (gcps, CRS.from_epsg(4326))


def _make_stack(
    tmp_path: Path,
    dn: Dict[str, np.ndarray],
    *,
    width: int,
    height: int,
    array_mask: Optional[np.ndarray] = None,
    item_overrides: Optional[Dict[str, Any]] = None,
    drop_assets: Tuple[str, ...] = (),
    extra_assets: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Tuple[RasterStack, Dict[str, Any]]:
    """A single-item, single-timestamp RasterStack wired for sar_backscatter tests.

    `dn`: {polarisation: (height, width) array}, one measurement/calibration/
    noise asset triplet synthesized per polarisation, all sharing one
    measurement GCP file and the real IPF>=2.90 fixture annotation XML.
    """
    polarisations = list(dn)
    measurement_path = tmp_path / "measurement.tif"
    _write_measurement_gcp_tiff(measurement_path)

    assets: Dict[str, Any] = {}
    fixture_map: Dict[str, bytes] = {}
    for pol in polarisations:
        assets[pol] = {"href": str(measurement_path)}
        cal_href, noise_href = f"fixture://calibration-{pol}", f"fixture://noise-{pol}"
        assets[f"schema-calibration-{pol}"] = {"href": cal_href}
        assets[f"schema-noise-{pol}"] = {"href": noise_href}
        fixture_map[cal_href] = _CALIBRATION_XML
        fixture_map[noise_href] = _NOISE_XML

    for key in drop_assets:
        assets.pop(key, None)
    if extra_assets:
        assets.update(extra_assets)

    properties: Dict[str, Any] = {
        "datetime": "2024-01-01T00:00:00Z",
        "sar:product_type": "GRD",
    }
    if item_overrides and "properties" in item_overrides:
        properties = item_overrides["properties"]

    # A real pystac.Item, because that is what load_collection carries -- its
    # assets are pystac.Asset objects (href as an attribute), not dicts. Testing
    # against dicts alone is exactly what let the original implementation pass
    # CI and then fail against a live backend.
    item = pystac.Item(
        id="S1TEST_0001",
        geometry=None,
        bbox=None,
        datetime=datetime(2024, 1, 1),
        properties=properties,
    )
    for name, fields in assets.items():
        extra = {k: v for k, v in fields.items() if k != "href"}
        item.add_asset(name, pystac.Asset(href=fields["href"], extra_fields=extra))

    stacked_dn = np.stack([dn[pol] for pol in polarisations]).astype("uint16")
    array = np.ma.MaskedArray(
        stacked_dn, mask=array_mask if array_mask is not None else False
    )
    image = ImageData(
        array,
        crs=CRS.from_epsg(4326),
        bounds=(0, 0, 1, 1),
        band_names=polarisations,
        band_descriptions=polarisations,
    )

    def task_fn() -> ImageData:
        return image

    stack = RasterStack(
        tasks=[(task_fn, _date_group_meta([item]))],
        timestamp_fn=lambda asset: asset["datetime"],
        width=width,
        height=height,
        bounds=(0.0, 0.0, 1.0, 1.0),
        dst_crs=CRS.from_epsg(4326),
        band_names=polarisations,
    )
    fetcher = _FixtureFetcher(fixture_map)
    return stack, {"fetcher": fetcher}


def _only_image(stack: RasterStack) -> ImageData:
    key = next(iter(stack.keys()))
    return stack[key]


# --------------------------------------------------------------------------- parameter validation


@pytest.mark.parametrize("coefficient", ["sigma0-terrain", "gamma0-terrain"])
def test_rejects_terrain_coefficients(tmp_path, coefficient):
    stack, options = _make_stack(tmp_path, {"vv": np.array([[100]])}, width=1, height=1)
    with pytest.raises(ProcessParameterInvalid, match="terrain-corrected"):
        sar_backscatter(stack, coefficient=coefficient, options=options)


def test_rejects_contributing_area(tmp_path):
    stack, options = _make_stack(tmp_path, {"vv": np.array([[100]])}, width=1, height=1)
    with pytest.raises(FeatureUnsupported):
        sar_backscatter(
            stack,
            coefficient="sigma0-ellipsoid",
            contributing_area=True,
            options=options,
        )


def test_rejects_local_incidence_angle(tmp_path):
    stack, options = _make_stack(tmp_path, {"vv": np.array([[100]])}, width=1, height=1)
    with pytest.raises(FeatureUnsupported):
        sar_backscatter(
            stack,
            coefficient="sigma0-ellipsoid",
            local_incidence_angle=True,
            options=options,
        )


def test_rejects_elevation_model(tmp_path):
    stack, options = _make_stack(tmp_path, {"vv": np.array([[100]])}, width=1, height=1)
    with pytest.raises(DigitalElevationModelInvalid):
        sar_backscatter(
            stack,
            coefficient="sigma0-ellipsoid",
            elevation_model="COPERNICUS_30",
            options=options,
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


def test_rejects_a_slice_that_mosaics_several_items(tmp_path):
    """load_collection mosaics all items sharing a datetime into one image.

    Calibration is per source item -- each has its own LUTs and geolocation --
    so an already-mosaicked blend of several must be refused rather than
    calibrated with one item's radiometry. This is the case ADR S7.10(b)
    (per-item calibration in the read path) would properly solve.
    """
    measurement_path = tmp_path / "measurement.tif"
    _write_measurement_gcp_tiff(measurement_path)

    def _item(item_id: str) -> pystac.Item:
        it = pystac.Item(
            id=item_id,
            geometry=None,
            bbox=None,
            datetime=datetime(2024, 1, 1),
            properties={"sar:product_type": "GRD"},
        )
        it.add_asset("vv", pystac.Asset(href=str(measurement_path)))
        it.add_asset("schema-calibration-vv", pystac.Asset(href="fixture://cal"))
        it.add_asset("schema-noise-vv", pystac.Asset(href="fixture://noise"))
        return it

    image = ImageData(
        np.ma.MaskedArray(np.ones((1, 1, 1), dtype="uint16")),
        crs=CRS.from_epsg(4326),
        bounds=(0, 0, 1, 1),
        band_names=["vv"],
        band_descriptions=["vv"],
    )
    stack = RasterStack(
        tasks=[(lambda: image, _date_group_meta([_item("A"), _item("B")]))],
        timestamp_fn=lambda asset: asset["datetime"],
        width=1,
        height=1,
        bounds=(0.0, 0.0, 1.0, 1.0),
        dst_crs=CRS.from_epsg(4326),
        band_names=["vv"],
    )

    with pytest.raises(ProcessParameterInvalid, match="mosaics 2 source items"):
        sar_backscatter(stack, coefficient="sigma0-ellipsoid")


# --------------------------------------------------------------------------- asset resolution


def test_rejects_missing_measurement_asset(tmp_path):
    stack, options = _make_stack(
        tmp_path, {"vv": np.array([[100]])}, width=1, height=1, drop_assets=("vv",)
    )
    with pytest.raises(ProcessParameterInvalid, match="no 'vv' measurement asset"):
        sar_backscatter(stack, coefficient="sigma0-ellipsoid", options=options)


def test_rejects_missing_calibration_asset(tmp_path):
    stack, options = _make_stack(
        tmp_path,
        {"vv": np.array([[100]])},
        width=1,
        height=1,
        drop_assets=("schema-calibration-vv",),
    )
    with pytest.raises(ProcessParameterInvalid, match="calibration annotation asset"):
        sar_backscatter(stack, coefficient="sigma0-ellipsoid", options=options)


def test_falls_back_to_href_pattern_when_schema_key_is_absent(tmp_path):
    """Missing the `schema-calibration-<pol>` key is fine if an asset's href
    still matches the annotation filename convention (ADR S7.3 step 2)."""
    stack, options = _make_stack(
        tmp_path,
        {"vv": np.array([[100]])},
        width=1,
        height=1,
        drop_assets=("schema-calibration-vv",),
        extra_assets={
            "product-calibration-vv": {
                "href": "https://example.com/annotation/calibration/calibration-x-vv-y.xml"
            }
        },
    )
    # Route the pattern-matched href to the same fixture bytes.
    options["fetcher"]._mapping[
        "https://example.com/annotation/calibration/calibration-x-vv-y.xml"
    ] = _CALIBRATION_XML

    result = sar_backscatter(stack, coefficient="sigma0-ellipsoid", options=options)
    assert _only_image(result).array is not None  # resolves and runs without error


def test_rejects_ambiguous_annotation_asset(tmp_path):
    stack, options = _make_stack(
        tmp_path,
        {"vv": np.array([[100]])},
        width=1,
        height=1,
        drop_assets=("schema-calibration-vv",),
        extra_assets={
            "a-calibration-vv": {"href": "s3://bucket/calibration-a-vv.xml"},
            "b-calibration-vv": {"href": "s3://bucket/calibration-b-vv.xml"},
        },
    )
    with pytest.raises(ProcessParameterInvalid, match="[Aa]mbiguous"):
        sar_backscatter(stack, coefficient="sigma0-ellipsoid", options=options)


def test_rejects_slc_product_type(tmp_path):
    stack, options = _make_stack(
        tmp_path,
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
        sar_backscatter(stack, coefficient="sigma0-ellipsoid", options=options)


def test_accepts_item_with_no_product_type_metadata(tmp_path):
    """Absent product-type metadata must not be gated on (capability, not identity)."""
    stack, options = _make_stack(
        tmp_path,
        {"vv": np.array([[100]])},
        width=1,
        height=1,
        item_overrides={"properties": {"datetime": "2024-01-01T00:00:00Z"}},
    )
    result = sar_backscatter(stack, coefficient="sigma0-ellipsoid", options=options)
    assert _only_image(result).count == 1


# --------------------------------------------------------------------------- band assembly


def test_band_assembly_names_dtype_and_shape(tmp_path):
    stack, options = _make_stack(tmp_path, {"vv": np.array([[500]])}, width=1, height=1)
    result = sar_backscatter(
        stack,
        coefficient="sigma0-ellipsoid",
        mask=True,
        ellipsoid_incidence_angle=True,
        options=options,
    )
    img = _only_image(result)
    assert img.band_descriptions == ["vv", "ellipsoid_incidence_angle", "mask"]
    assert img.array.dtype == np.float32
    assert img.array.shape == (3, 1, 1)


def test_band_assembly_without_optional_bands(tmp_path):
    stack, options = _make_stack(
        tmp_path, {"vv": np.array([[500]]), "vh": np.array([[300]])}, width=1, height=1
    )
    result = sar_backscatter(stack, coefficient="sigma0-ellipsoid", options=options)
    img = _only_image(result)
    assert img.band_descriptions == ["vv", "vh"]
    assert img.array.shape == (2, 1, 1)


# --------------------------------------------------------------------------- radiometry


def test_null_coefficient_returns_uncalibrated_power(tmp_path):
    stack, options = _make_stack(tmp_path, {"vv": np.array([[100]])}, width=1, height=1)
    result = sar_backscatter(
        stack, coefficient=None, noise_removal=False, options=options
    )
    value = _only_image(result).array.data[0, 0, 0]
    assert value == pytest.approx(100.0**2, rel=1e-5)


def test_noise_removal_differs_measurably_at_low_backscatter(tmp_path):
    """ADR acceptance criterion 5: noise_removal true/false differ measurably
    where noise dominates (low DN)."""
    stack_on, options_on = _make_stack(
        tmp_path, {"vv": np.array([[10]])}, width=1, height=1
    )
    stack_off, options_off = _make_stack(
        tmp_path, {"vv": np.array([[10]])}, width=1, height=1
    )

    with_noise = sar_backscatter(
        stack_on, coefficient="sigma0-ellipsoid", noise_removal=True, options=options_on
    )
    without_noise = sar_backscatter(
        stack_off,
        coefficient="sigma0-ellipsoid",
        noise_removal=False,
        options=options_off,
    )

    on_value = _only_image(with_noise).array.data[0, 0, 0]
    off_value = _only_image(without_noise).array.data[0, 0, 0]
    assert on_value == 0.0  # clamped: noise (~1200+) swamps dn**2 (100)
    assert off_value > 0.0


def test_noise_removal_agrees_at_high_backscatter(tmp_path):
    """...and agree closely where the signal dwarfs the noise floor (high DN)."""
    stack_on, options_on = _make_stack(
        tmp_path, {"vv": np.array([[2000]])}, width=1, height=1
    )
    stack_off, options_off = _make_stack(
        tmp_path, {"vv": np.array([[2000]])}, width=1, height=1
    )

    with_noise = sar_backscatter(
        stack_on, coefficient="sigma0-ellipsoid", noise_removal=True, options=options_on
    )
    without_noise = sar_backscatter(
        stack_off,
        coefficient="sigma0-ellipsoid",
        noise_removal=False,
        options=options_off,
    )

    on_value = _only_image(with_noise).array.data[0, 0, 0]
    off_value = _only_image(without_noise).array.data[0, 0, 0]
    assert on_value == pytest.approx(off_value, rel=1e-2)


def test_mask_band_reflects_dn_zero_border(tmp_path):
    stack, options = _make_stack(
        tmp_path, {"vv": np.array([[0, 500]])}, width=2, height=1
    )
    result = sar_backscatter(
        stack, coefficient="sigma0-ellipsoid", mask=True, options=options
    )
    img = _only_image(result)
    mask_band = img.array.data[img.band_descriptions.index("mask")]
    assert mask_band[0, 0] == 0.0  # DN == 0 -> invalid/no-data
    assert mask_band[0, 1] == 1.0


def test_combines_stack_mask_with_dn_validity(tmp_path):
    """A pixel the *stack* already marked invalid must stay invalid even if
    its DN happens to be > 0 (ADR S7.3 step 7: preserve the stack's own mask)."""
    array_mask = np.array([[[True, False]]])  # (band=1, height=1, width=2)
    stack, options = _make_stack(
        tmp_path,
        {"vv": np.array([[500, 500]])},
        width=2,
        height=1,
        array_mask=array_mask,
    )
    result = sar_backscatter(
        stack, coefficient="sigma0-ellipsoid", mask=True, options=options
    )
    img = _only_image(result)
    mask_band = img.array.data[img.band_descriptions.index("mask")]
    assert mask_band[0, 0] == 0.0  # masked upstream despite dn > 0
    assert mask_band[0, 1] == 1.0
    assert bool(img.array.mask[0, 0, 0]) is True
    assert bool(img.array.mask[0, 0, 1]) is False


def test_ellipsoid_incidence_angle_band_is_plausible_degrees(tmp_path):
    stack, options = _make_stack(tmp_path, {"vv": np.array([[500]])}, width=1, height=1)
    result = sar_backscatter(
        stack,
        coefficient="sigma0-ellipsoid",
        ellipsoid_incidence_angle=True,
        options=options,
    )
    img = _only_image(result)
    angle = img.array.data[img.band_descriptions.index("ellipsoid_incidence_angle")]
    assert 0.0 < float(angle[0, 0]) < 90.0


# --------------------------------------------------------------------------- integration


def test_sar_backscatter_is_registered():
    assert "sar_backscatter" in process_registry
    assert process_registry["sar_backscatter"].spec["id"] == "sar_backscatter"


def test_sar_backscatter_runs_through_the_real_process_graph(tmp_path):
    """Discoverable and callable as an actual graph node, not just a plain
    Python function -- the real executor path (see
    test_apply_graph_integration.py for the pattern)."""
    stack, options = _make_stack(tmp_path, {"vv": np.array([[500]])}, width=1, height=1)
    pg = {
        "sar": {
            "process_id": "sar_backscatter",
            "arguments": {
                "data": {"from_parameter": "data"},
                "coefficient": "sigma0-ellipsoid",
                "options": options,
            },
            "result": True,
        }
    }
    callable_ = OpenEOProcessGraph(pg_data={"process_graph": pg}).to_callable(
        process_registry=process_registry
    )
    result = callable_(named_parameters={"data": stack})
    assert _only_image(result).band_descriptions == ["vv"]
