"""
Test ParameterReference resolution in @process decorator.

Reproduces the issue where ParameterReference objects are not being resolved
when they come through positional_parameters or named_parameters.
"""

import pytest
from openeo_pg_parser_networkx.pg_schema import ParameterReference

from titiler.openeo.processes.implementations.core import process


def test_parameter_reference_in_kwargs():
    """Test that ParameterReference in kwargs gets resolved."""
    call_log = []

    @process
    def test_func(data, index: int = None):
        call_log.append({"data": data, "index": index})
        return data

    # Setup named_parameters with the actual data
    named_params = {"data": [1, 2, 3, 4, 5], "index": 2}

    # Create a ParameterReference for data
    data_ref = ParameterReference(from_parameter="data")

    # Call with ParameterReference in kwargs
    _ = test_func(data=data_ref, index=2, named_parameters=named_params)

    # Verify the function received the resolved value, not the ParameterReference
    assert len(call_log) == 1
    assert call_log[0]["data"] == [1, 2, 3, 4, 5]
    assert not isinstance(call_log[0]["data"], ParameterReference)


def test_parameter_reference_in_positional_parameters():
    """Test that ParameterReference in positional_parameters gets resolved."""
    call_log = []

    @process
    def test_func(data, index: int = None):
        call_log.append({"data": data, "index": index})
        return data

    # Setup named_parameters with the actual data
    named_params = {"data": [1, 2, 3, 4, 5], "index": 2}

    # Create a ParameterReference for data
    data_ref = ParameterReference(from_parameter="data")

    # Call with ParameterReference in positional args
    # positional_parameters maps parameter name to position in args
    _ = test_func(
        data_ref,  # args[0]
        positional_parameters={"data": 0},
        named_parameters=named_params,
        index=2,
    )

    # Verify the function received the resolved value
    assert len(call_log) == 1
    assert call_log[0]["data"] == [1, 2, 3, 4, 5]
    assert not isinstance(call_log[0]["data"], ParameterReference)


def test_parameter_reference_only_in_named_parameters():
    """Test when parameter is only in named_parameters (not in kwargs or positional)."""
    call_log = []

    @process
    def test_func(data, index: int = None):
        call_log.append({"data": data, "index": index})
        return data

    # Setup: data is only in named_parameters
    named_params = {"data": [1, 2, 3, 4, 5], "index": 2}

    # Call without passing data in kwargs or args
    # The decorator should extract it from named_parameters
    _ = test_func(index=2, named_parameters=named_params)

    # Verify the function received the value from named_parameters
    assert len(call_log) == 1
    assert call_log[0]["data"] == [1, 2, 3, 4, 5]
    assert call_log[0]["index"] == 2


def test_idempotent_recursive_calls():
    """Test that the decorator is idempotent when called recursively."""
    call_count = []

    @process
    def inner_func(value):
        call_count.append(("inner", value))
        return value * 2

    @process
    def outer_func(data):
        call_count.append(("outer", data))
        # Call inner function (recursive @process call)
        result = inner_func(value=data[0], named_parameters={"value": data[0]})
        return result

    # Setup
    named_params = {"data": [5, 10, 15]}

    # Call outer function
    result = outer_func(data=[5, 10, 15], named_parameters=named_params)

    # Verify both functions received correct values
    assert len(call_count) == 2
    assert call_count[0] == ("outer", [5, 10, 15])
    assert call_count[1] == ("inner", 5)
    assert result == 10


def test_parameter_reference_with_positional_and_named_conflict():
    """Test resolution when same parameter appears in both positional_parameters and named_parameters."""
    call_log = []

    @process
    def test_func(data, index: int = None):
        call_log.append({"data": data, "index": index})
        return data

    # Setup: named_parameters has the correct resolved value
    named_params = {"data": [1, 2, 3, 4, 5], "index": 2}

    # But positional_parameters maps to a scalar value (wrong value)
    scalar_value = 0

    # This simulates the issue: positional_parameters[0] = scalar,
    # but named_parameters["data"] = correct array
    _ = test_func(
        scalar_value,  # args[0] - wrong value
        positional_parameters={"data": 0},
        named_parameters=named_params,  # has correct value
        index=2,
    )

    # The decorator should prefer the resolved value from named_parameters
    # over the scalar from args when named_parameters has a non-ParameterReference value
    assert len(call_log) == 1
    # This is the key test: should get the array from named_parameters, not the scalar
    assert call_log[0]["data"] == [1, 2, 3, 4, 5]
    assert call_log[0]["data"] != 0


def test_parameter_references_nested_in_context_list():
    """ParameterReferences inside a context list must be resolved before the callback sees them.

    This is the spec-correct pattern for passing UDP-level parameters into a callback:
        context=[{"from_parameter": "indicator"}, {"from_parameter": "min_value"}]
    The parser produces context=[ParameterReference("indicator"), ParameterReference("min_value")].
    _resolve_kwargs must resolve those nested refs, not pass them through raw.
    See: https://github.com/Open-EO/openeo-processes/blob/master/apply_dimension.json
    """
    received_context = []

    @process
    def callback_process(data, context=None):
        received_context.append(context)
        return data

    named_params = {"indicator": 3, "min_value": 0.1}
    context_with_refs = [
        ParameterReference(from_parameter="indicator"),
        ParameterReference(from_parameter="min_value"),
    ]

    callback_process(
        data=[1, 2, 3],
        context=context_with_refs,
        named_parameters=named_params,
    )

    assert len(received_context) == 1
    ctx = received_context[0]
    assert ctx == [3, 0.1], f"Expected resolved values [3, 0.1], got {ctx!r}"
    assert not any(isinstance(v, ParameterReference) for v in ctx)


def test_parameter_references_nested_in_context_dict():
    """ParameterReferences inside a context dict must also be resolved."""
    received_context = []

    @process
    def callback_process(data, context=None):
        received_context.append(context)
        return data

    named_params = {"scale": 2.5}
    context_with_refs = {"multiplier": ParameterReference(from_parameter="scale")}

    callback_process(
        data=[1, 2, 3],
        context=context_with_refs,
        named_parameters=named_params,
    )

    assert received_context[0] == {"multiplier": 2.5}


def test_parameter_reference_as_context_scalar():
    """context itself may be a bare ParameterReference (Option A: single-value context).

    This corresponds to the pattern:
        "context": {"from_parameter": "indicator"}
    where the callback receives the resolved scalar directly as context.
    """
    received_context = []

    @process
    def callback_process(data, context=None):
        received_context.append(context)
        return data

    named_params = {"indicator": 3}

    callback_process(
        data=[1, 2, 3],
        context=ParameterReference(from_parameter="indicator"),
        named_parameters=named_params,
    )

    assert received_context[0] == 3
    assert not isinstance(received_context[0], ParameterReference)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
