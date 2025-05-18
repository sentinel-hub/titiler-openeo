"""Tests for the get_param_item process implementation and API integration."""

import pytest

from titiler.openeo.errors import ProcessParameterInvalid, ProcessParameterMissing
from titiler.openeo.processes.implementations.get_param_item import get_param_item


def test_get_param_item_basic():  # Test basic dictionary access
    param = {"metadata": {"resolution": 10}}

    assert get_param_item(param, "$.metadata.resolution") == 10

    # Test array access
    param = {"bands": ["red", "green", "blue"]}
    assert get_param_item(param, "$.bands[0]") == "red"

    # Test nested structures
    param = {
        "metadata": {
            "bands": [
                {"name": "red", "wavelength": 665},
                {"name": "nir", "wavelength": 865},
            ]
        }
    }
    assert get_param_item(param, "$.metadata.bands[1].wavelength") == 865


def test_get_param_item_errors():  # Test missing path
    param = {"metadata": {"resolution": 10}}

    with pytest.raises(ProcessParameterMissing) as excinfo:
        get_param_item(param, "$.nonexistent")
        assert "not found in parameter" in str(excinfo.value)

    # Test invalid JSONPath syntax
    with pytest.raises(ProcessParameterInvalid) as excinfo:
        get_param_item(param, "invalid[path")
    assert "Invalid JSONPath expression" in str(excinfo.value)


def test_get_param_item_validation():  # Test invalid key type (non-string)
    param = {1: "value"}
    with pytest.raises(ProcessParameterInvalid) as excinfo:
        get_param_item(param, "$[1]")
        assert "only string keys are allowed" in str(excinfo.value)

    # Test invalid value type (function)
    param = {"func": lambda x: x}
    with pytest.raises(ProcessParameterInvalid) as excinfo:
        get_param_item(param, "$.func")
    assert "only dict, list, and JSON-compatible scalar types are allowed" in str(
        excinfo.value
    )

    #    Test invalid value type (custom object)
    class CustomObj:
        pass

    param = {"obj": CustomObj()}
    with pytest.raises(ProcessParameterInvalid) as excinfo:
        get_param_item(param, "$.obj")
    assert "only dict, list, and JSON-compatible scalar types are allowed" in str(
        excinfo.value
    )


def test_get_param_item_scalar_types():  # Test all valid scalar types
    param = {
        "string": "test",
        "integer": 42,
        "float": 3.14,
        "boolean": True,
        "null": None,
        "array": [1, 2, 3],
        "object": {"key": "value"},
    }

    assert get_param_item(param, "$.string") == "test"
    assert get_param_item(param, "$.integer") == 42
    assert get_param_item(param, "$.float") == 3.14
    assert get_param_item(param, "$.boolean") is True
    assert get_param_item(param, "$.null") is None
    assert get_param_item(param, "$.array") == [1, 2, 3]
    assert get_param_item(param, "$.object") == {"key": "value"}


def test_get_param_item_complex_paths():
    param = {
        "data": {
            "bands": [
                {"name": "red", "values": [1, 2, 3]},
                {"name": "green", "values": [4, 5, 6]},
                {"name": "blue", "values": [7, 8, 9]},
            ]
        }
    }

    # Test nested array access
    assert get_param_item(param, "$.data.bands[0].values[1]") == 2

    # Test deep nesting
    complex_param = {"level1": {"level2": {"level3": {"value": 42}}}}
    assert get_param_item(complex_param, "$.level1.level2.level3.value") == 42


def test_get_param_item_empty_structures():
    # Test empty structures
    assert get_param_item({}, "$.metadata") is None
    assert get_param_item({"arr": []}, "$.arr[0]") is None


def test_get_param_item_integration(app_with_auth):
    """Test get_param_item process using the POST /result endpoint."""
    process_graph = {
        "process": {
            "process_graph": {
                "get_item": {
                    "process_id": "get_param_item",
                    "arguments": {
                        "parameter": {
                            "metadata": {
                                "bands": ["red", "green", "blue"],
                                "resolution": 10,
                            }
                        },
                        "path": "$.metadata.bands[0]",
                    },
                    "result": True,
                }
            }
        }
    }

    response = app_with_auth.post("/result", json=process_graph)
    assert response.status_code == 200
    assert response.text == "red"
