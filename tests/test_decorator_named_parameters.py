"""
Pure unit tests for the @process decorator's named_parameters handling.

Tests the fix for issue #186 where the @process decorator was consuming
named_parameters but not forwarding it to functions that expected it.

These are pure unit tests that:
- Test only the decorator behavior in isolation
- Use simple test functions without external dependencies
- Don't rely on real implementations or mocks
- Focus solely on the decorator's parameter passing logic
"""

from typing import Optional

import pytest

from titiler.openeo.processes.implementations.core import process


def test_decorator_passes_named_parameters_when_function_expects_it():
    """Test that @process decorator passes named_parameters to functions that declare it."""
    # Track what the function receives
    received_kwargs = {}

    def real_func(id: str, value: int = None, named_parameters: dict = None):
        """Real function with named_parameters."""
        received_kwargs.update(
            {"id": id, "value": value, "named_parameters": named_parameters}
        )
        return "result"

    # Wrap with decorator
    wrapped = process(real_func)

    # Call with named_parameters
    test_params = {"param1": "value1", "param2": 42}
    wrapped(id="test", value=10, named_parameters=test_params)

    # Verify named_parameters was passed to the function
    assert "named_parameters" in received_kwargs
    assert received_kwargs["named_parameters"] == test_params


def test_decorator_does_not_pass_named_parameters_when_function_does_not_expect_it():
    """Test that @process decorator doesn't pass named_parameters to functions that don't declare it."""
    # Track what the function receives
    received_kwargs = {}

    def real_func(id: str, value: int = None):
        """Real function without named_parameters."""
        received_kwargs.update({"id": id, "value": value})
        return "result"

    # Wrap with decorator
    wrapped = process(real_func)

    # Call with named_parameters (decorator should consume but not pass)
    test_params = {"param1": "value1"}
    wrapped(id="test", value=10, named_parameters=test_params)

    # Verify named_parameters was NOT passed to the function
    assert "named_parameters" not in received_kwargs
    assert received_kwargs["id"] == "test"
    assert received_kwargs["value"] == 10


def test_decorator_with_real_function_expecting_named_parameters():
    """Test decorator with a real function (not mocked) that expects named_parameters."""
    call_log = []

    def process_func(
        input_data: str,
        threshold: Optional[float] = None,
        named_parameters: Optional[dict] = None,
    ):
        """Real function that expects named_parameters."""
        call_log.append(
            {
                "input_data": input_data,
                "threshold": threshold,
                "named_parameters": named_parameters,
            }
        )
        return {"processed": True, "params": named_parameters}

    # Wrap with decorator
    wrapped = process(process_func)

    # Call with named_parameters
    test_params = {"key1": "val1", "key2": 123}
    result = wrapped(input_data="data", threshold=0.5, named_parameters=test_params)

    # Verify function received named_parameters
    assert len(call_log) == 1
    assert call_log[0]["named_parameters"] == test_params
    assert result["params"] == test_params


def test_decorator_with_real_function_not_expecting_named_parameters():
    """Test decorator with a real function (not mocked) that doesn't expect named_parameters."""
    call_log = []

    def simple_func(input_data: str, threshold: Optional[float] = None):
        """Real function without named_parameters."""
        call_log.append(
            {
                "input_data": input_data,
                "threshold": threshold,
            }
        )
        return {"processed": True}

    # Wrap with decorator
    wrapped = process(simple_func)

    # Call with named_parameters
    test_params = {"key1": "val1"}
    result = wrapped(input_data="data", threshold=0.5, named_parameters=test_params)

    # Verify function was called successfully without named_parameters
    assert len(call_log) == 1
    assert "named_parameters" not in call_log[0]
    assert call_log[0]["input_data"] == "data"
    assert result["processed"] is True


def test_decorator_passes_empty_dict_when_named_parameters_not_provided():
    """Test that decorator passes empty dict when named_parameters is not provided."""
    call_log = []

    def func_with_params(id: str, named_parameters: Optional[dict] = None):
        """Function expecting named_parameters."""
        call_log.append({"id": id, "named_parameters": named_parameters})
        return {"id": id}

    wrapped = process(func_with_params)

    # Call WITHOUT named_parameters
    wrapped(id="test")

    # Function should receive empty dict (decorator initializes it)
    assert len(call_log) == 1
    assert call_log[0]["named_parameters"] == {}


def test_decorator_with_multiple_parameters_expecting_named_parameters():
    """Test decorator with function that has many parameters plus named_parameters."""
    call_log = []

    def complex_func(
        id: str,
        spatial_extent: Optional[dict] = None,
        temporal_extent: Optional[dict] = None,
        bands: Optional[list] = None,
        properties: Optional[dict] = None,
        width: Optional[int] = 1024,
        named_parameters: Optional[dict] = None,
    ):
        """Complex function signature similar to load_collection."""
        call_log.append(
            {
                "id": id,
                "bands": bands,
                "named_parameters": named_parameters,
            }
        )
        return {"result": "success", "named_parameters": named_parameters}

    wrapped = process(complex_func)

    # Call with various parameters including named_parameters
    test_params = {"cloud_cover": 20, "platform": "sentinel-2"}
    result = wrapped(
        id="collection-1",
        bands=["B04", "B03"],
        width=2048,
        named_parameters=test_params,
    )

    # Verify all parameters passed correctly
    assert len(call_log) == 1
    assert call_log[0]["id"] == "collection-1"
    assert call_log[0]["bands"] == ["B04", "B03"]
    assert call_log[0]["named_parameters"] == test_params
    assert result["named_parameters"] == test_params


def test_decorator_preserves_function_behavior():
    """Test that decorator doesn't alter the core function behavior."""

    def add_numbers(a: int, b: int, named_parameters: Optional[dict] = None) -> int:
        """Simple math function."""
        return a + b

    wrapped = process(add_numbers)

    # Call the wrapped function
    result = wrapped(a=5, b=10, named_parameters={"meta": "data"})

    # Verify the function logic still works
    assert result == 15


def test_decorator_with_method_signature():
    """Test decorator works with instance methods (like LoadCollection.load_collection)."""
    call_log = []

    class MockClass:
        def method_with_params(
            self,
            id: str,
            value: Optional[int] = None,
            named_parameters: Optional[dict] = None,
        ):
            """Instance method expecting named_parameters."""
            call_log.append(
                {
                    "id": id,
                    "value": value,
                    "named_parameters": named_parameters,
                }
            )
            return {"id": id, "named_parameters": named_parameters}

    instance = MockClass()
    wrapped_method = process(instance.method_with_params)

    # Call wrapped method
    test_params = {"key": "value"}
    result = wrapped_method(id="test", value=42, named_parameters=test_params)

    # Verify named_parameters was passed
    assert len(call_log) == 1
    assert call_log[0]["named_parameters"] == test_params
    assert result["named_parameters"] == test_params


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
