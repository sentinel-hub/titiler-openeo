"""Catalogue-contract tests for sar_backscatter asset resolution (ADR S7.9).

Table-driven over committed, trimmed real STAC items from the three catalogues
this project targets (`tests/fixtures/sar/items/`, fetched live 2026-07-30),
asserting the ADR S1.7 requirements: annotation siblings resolve for every
polarisation, real GRD items are never rejected by the product-type gate, and
a misleading `proj:*` (present on Earth Search and Planetary Computer,
confirmed fabricated/bbox-derived) is never consulted. Adding a catalogue
means adding a row below and a fixture, per the ADR.

**What this does not verify: GCP presence.** GCPs live in the measurement
raster's own header, not STAC metadata, so a committed-JSON fixture cannot
assert it -- that would need a live, credentialed read of the actual asset.
It is already measured live per catalogue (ADR S1.5's table) and was
re-confirmed empirically against a live backend in #347's validation
notebook (a real calibrated composite; median open-water sigma0 at
-24.6 dB, matching the ADR's expected -20..-25 dB band). Faking that check
against a fixture that cannot carry the property would be worse than not
having it.
"""

import inspect
import json
from pathlib import Path

import pytest

from titiler.openeo.processes.implementations import sar as sar_module
from titiler.openeo.processes.implementations.sar import (
    _check_product_type,
    _resolve_polarisation_assets,
)

FIXTURES = Path(__file__).parent / "fixtures" / "sar" / "items"

CATALOGUES = [
    pytest.param("cdse.json", ["vv", "vh"], id="CDSE"),
    pytest.param("earth_search.json", ["vv", "vh"], id="EarthSearch"),
    pytest.param("planetary_computer.json", ["hh", "hv"], id="PlanetaryComputer"),
]


def _load(fixture_name: str) -> dict:
    return json.loads((FIXTURES / fixture_name).read_text())


@pytest.mark.parametrize("fixture_name,polarisations", CATALOGUES)
def test_annotation_siblings_resolve(fixture_name, polarisations):
    """Every requested polarisation's measurement/calibration/noise asset resolves."""
    item = _load(fixture_name)
    for pol in polarisations:
        measurement_href, calibration_href, noise_href = _resolve_polarisation_assets(
            item, pol
        )
        assert measurement_href and measurement_href.endswith(".tiff")
        assert calibration_href and calibration_href.endswith(".xml")
        assert noise_href and noise_href.endswith(".xml")
        assert len({measurement_href, calibration_href, noise_href}) == 3


@pytest.mark.parametrize("fixture_name,_polarisations", CATALOGUES)
def test_product_type_gate_accepts_real_items(fixture_name, _polarisations):
    """Real GRD items from every catalogue clear the capability gate.

    CDSE has no `sar:product_type` at all (only `product:type`, a differently
    formatted field this process does not key on); Earth Search and Planetary
    Computer both set `sar:product_type: GRD`. Both must be accepted --
    gate on capability (asset resolution, tested above), not identity.
    """
    _check_product_type(_load(fixture_name))  # must not raise


@pytest.mark.parametrize(
    "fixture_name", ["earth_search.json", "planetary_computer.json"]
)
def test_fixture_documents_the_misleading_proj_transform(fixture_name):
    """Earth Search and Planetary Computer both publish a bbox-derived
    `proj:transform` that is fiction for SAR geometry (ADR S1.7). Pinning its
    presence here means a future re-fetch that silently loses it doesn't
    quietly stop testing the case this fixture exists for.
    """
    item = _load(fixture_name)
    assert "proj:transform" in item["properties"]


def test_cdse_fixture_documents_the_absence_of_proj_metadata():
    """CDSE publishes no `proj:*` at all (ADR S1.7) -- the cleanest catalogue
    to confirm the resolution path needs none of it."""
    item = _load("cdse.json")
    assert not any(k.startswith("proj:") for k in item["properties"])


def test_resolution_never_reads_item_proj_fields():
    """Structural guard: sar.py's asset-resolution code must never key off
    `proj:*` -- geometry comes only from `geocode.get_gcps` (the measurement
    asset's own header), never from item/asset metadata (ADR S1.7). This is
    a property of the code, not of any one fixture, so it is asserted once
    here rather than duplicated per catalogue.
    """
    assert "proj:" not in inspect.getsource(sar_module)
