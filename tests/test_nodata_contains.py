"""Tests for the is_nodata and array_contains processes."""

import numpy as np
import pytest
from openeo_pg_parser_networkx.graph import OpenEOProcessGraph

from titiler.openeo.processes import process_registry
from titiler.openeo.processes.implementations.arrays import array_contains
from titiler.openeo.processes.implementations.logic import is_nodata


@pytest.mark.parametrize(
    "x,expected",
    [
        (1, False),
        ("Test", False),
        (None, True),
        ([None, None], False),
        (np.ma.masked, True),
    ],
)
def test_is_nodata_scalars(x, expected):
    """Spec examples and the masked constant."""
    assert is_nodata(x) is expected


def test_is_nodata_masked_array():
    """A masked array gives its mask, element by element."""
    data = np.ma.masked_array([[1, 2], [3, 4]], mask=[[True, False], [False, True]])
    result = is_nodata(data)
    np.testing.assert_array_equal(result, [[True, False], [False, True]])
    assert not isinstance(result, np.ma.MaskedArray)


def test_is_nodata_plain_array_and_nan():
    """A plain array has no no-data, NaN included."""
    result = is_nodata(np.array([1.0, np.nan]))
    np.testing.assert_array_equal(result, [False, False])


@pytest.mark.parametrize(
    "data,value,expected",
    [
        ([1, 2, 3], 2, True),
        (["A", "B", "C"], "b", False),
        ([False, False], False, True),
        ([True, True], False, False),
        ([1, 2, 3], "2", False),
        ([1, 2, None], None, False),
        ([[1, 2], [3, 4]], 2, False),
        ([1, 2.0], 2, True),
        ([1, 0], False, False),
        ([True], 1, False),
        ([], 1, False),
    ],
)
def test_array_contains_lists(data, value, expected):
    """Spec and conformance-suite examples, with strict typing."""
    assert array_contains(data, value) is expected


def test_array_contains_1d_array():
    """A 1-D array gives a boolean."""
    assert array_contains(np.array([1, 2, 3]), 2) is True
    assert array_contains(np.array([1, 2, 3]), 5) is False
    assert array_contains(np.array([1, 2, 3]), "2") is False
    assert array_contains(np.array([1, 2, 3]), True) is False


def test_array_contains_reduces_leading_axis():
    """A (bands, H, W) array is reduced over the band axis; masked values never match."""
    data = np.ma.masked_array(
        [[[1, 2], [3, 4]], [[5, 2], [7, 8]]],
        mask=[[[False, False], [False, False]], [[False, True], [False, False]]],
    )
    np.testing.assert_array_equal(
        array_contains(data, 5), [[True, False], [False, False]]
    )
    np.testing.assert_array_equal(
        array_contains(data, 2), [[False, True], [False, False]]
    )
    # The masked value 2 at (band 1, 0, 1) does not count; band 0 still has it.
    masked_only = np.ma.masked_array([[9], [5]], mask=[[False], [True]])
    np.testing.assert_array_equal(array_contains(masked_only, 5), [False])


def test_array_contains_list_of_arrays():
    """A list of band arrays is stacked on a leading axis."""
    result = array_contains([np.array([1, 2]), np.array([3, 1])], 3)
    np.testing.assert_array_equal(result, [True, False])


def test_processes_registered():
    """Both processes are in the registry with their specifications."""
    for name in ("is_nodata", "array_contains"):
        assert process_registry[name].spec["id"] == name


def test_array_contains_via_graph():
    """array_contains runs through the real executor."""
    pg = {
        "contains": {
            "process_id": "array_contains",
            "arguments": {"data": [1, 2, 3], "value": 2},
        },
        "nodata": {
            "process_id": "is_nodata",
            "arguments": {"x": {"from_node": "contains"}},
        },
        "not": {
            "process_id": "eq",
            "arguments": {"x": {"from_node": "nodata"}, "y": False},
            "result": True,
        },
    }
    callable_ = OpenEOProcessGraph(pg_data={"process_graph": pg}).to_callable(
        process_registry=process_registry
    )
    assert callable_(named_parameters={}) is True
