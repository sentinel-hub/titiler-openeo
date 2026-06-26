"""Tests for the rename_labels process."""

from datetime import datetime

import numpy as np
import pytest
from openeo_pg_parser_networkx.graph import OpenEOProcessGraph
from rio_tiler.models import ImageData

from titiler.openeo.errors import OpenEOException
from titiler.openeo.processes import process_registry
from titiler.openeo.processes.implementations.arrays import rename_labels
from titiler.openeo.processes.implementations.data_model import RasterStack


def _stack(band_descriptions=None):
    img = ImageData(
        np.ma.array(np.arange(3 * 2 * 2, dtype="float32").reshape(3, 2, 2)),
        band_descriptions=band_descriptions or ["b1", "b2", "b3"],
    )
    return RasterStack.from_images({datetime(2024, 4, 1): img})


def test_registered_in_process_registry():
    assert "rename_labels" in process_registry[None]


def test_rename_bands_by_position():
    out = rename_labels(_stack(), "bands", target=["B04", "B08", "SCL"])
    assert out.first.band_descriptions == ["B04", "B08", "SCL"]
    # data is unchanged
    np.testing.assert_array_equal(out.first.array, _stack().first.array)


def test_rename_bands_with_source_subset():
    out = rename_labels(_stack(), "bands", target=["RED", "NIR"], source=["b1", "b3"])
    assert out.first.band_descriptions == ["RED", "b2", "NIR"]


def test_rename_spectral_alias():
    out = rename_labels(_stack(), "spectral", target=["a", "b", "c"])
    assert out.first.band_descriptions == ["a", "b", "c"]


def test_rename_unnamed_bands_by_position():
    # band_descriptions absent -> still renamable by position
    img = ImageData(np.ma.array(np.zeros((2, 2, 2), "float32")), band_descriptions=[])
    stack = RasterStack.from_images({datetime(2024, 4, 1): img})
    out = rename_labels(stack, "bands", target=["x", "y"])
    assert out.first.band_descriptions == ["x", "y"]


def test_rename_temporal_by_position():
    out = rename_labels(_stack(), "t", target=["2025-01-01"])
    assert list(out.keys()) == [datetime(2025, 1, 1)]


def test_rename_temporal_with_source():
    s = RasterStack.from_images(
        {
            datetime(2024, 4, 1): ImageData(
                np.ma.array(np.zeros((1, 2, 2), "float32"))
            ),
            datetime(2024, 5, 1): ImageData(np.ma.array(np.ones((1, 2, 2), "float32"))),
        }
    )
    out = rename_labels(s, "t", target=["2030-01-01"], source=["2024-05-01"])
    keys = sorted(out.keys())
    assert keys == [datetime(2024, 4, 1), datetime(2030, 1, 1)]


def test_label_mismatch():
    with pytest.raises(OpenEOException) as exc:
        rename_labels(_stack(), "bands", target=["a", "b"], source=["b1"])
    assert exc.value.code == "LabelMismatch"


def test_label_mismatch_by_position():
    with pytest.raises(OpenEOException) as exc:
        rename_labels(_stack(), "bands", target=["a", "b"])  # 2 != 3 bands
    assert exc.value.code == "LabelMismatch"


def test_labels_not_enumerated_missing_source():
    with pytest.raises(OpenEOException) as exc:
        rename_labels(_stack(), "bands", target=["x"], source=["does-not-exist"])
    assert exc.value.code == "LabelsNotEnumerated"


def test_label_exists_on_collision():
    with pytest.raises(OpenEOException) as exc:
        rename_labels(_stack(), "bands", target=["b2", "b2", "b3"])
    assert exc.value.code == "LabelExists"


def test_unknown_dimension():
    with pytest.raises(OpenEOException) as exc:
        rename_labels(_stack(), "x", target=["a"])
    assert exc.value.code == "DimensionNotAvailable"


def test_rename_labels_via_process_graph():
    pg = {
        "process_graph": {
            "r": {
                "process_id": "rename_labels",
                "arguments": {
                    "data": {"from_parameter": "data"},
                    "dimension": "bands",
                    "target": ["B04", "B08", "SCL"],
                },
                "result": True,
            }
        }
    }
    fn = OpenEOProcessGraph(pg_data=pg).to_callable(process_registry=process_registry)
    out = fn(named_parameters={"data": _stack()})
    assert out.first.band_descriptions == ["B04", "B08", "SCL"]
