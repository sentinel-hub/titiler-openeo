"""Process decorator for OpenEO parameter resolution."""

import inspect
import logging
from functools import wraps
from typing import Any, Dict, Optional, Tuple, Union, get_args, get_origin

from openeo_pg_parser_networkx.pg_schema import ParameterReference
from pydantic import TypeAdapter, ValidationError

from ...errors import ProcessParameterMissing
from .data_model import LazyRasterStack

logger = logging.getLogger(__name__)


def _handle_positional_parameters(
    args: Tuple[Any, ...],
    positional_parameters: Dict[str, int],
    named_parameters: Dict[str, Any],
) -> None:
    """Map positional parameters to named parameters.

    Args:
        args: Tuple of positional arguments
        positional_parameters: Maps parameter names to positions
        named_parameters: Dictionary to store mapped parameters
    """
    for arg_name, i in positional_parameters.items():
        named_parameters[arg_name] = args[i]


def _resolve_positional_args(
    args: Tuple[Any, ...],
    named_parameters: Dict[str, Any],
    func_name: str,
) -> Tuple[Any, ...]:
    """Resolve positional arguments from parameter references.

    Args:
        args: Tuple of positional arguments
        named_parameters: Dictionary of parameter values
        func_name: Name of the function for error messages

    Returns:
        Tuple of resolved arguments

    Raises:
        ProcessParameterMissing: If a parameter reference cannot be resolved
    """
    resolved_args = []
    for arg in args:
        if isinstance(arg, ParameterReference):
            if arg.from_parameter in named_parameters:
                resolved_args.append(named_parameters[arg.from_parameter])
            else:
                raise ProcessParameterMissing(
                    f"Error: Process Parameter {arg.from_parameter} was missing for process {func_name}"
                )
    return tuple(resolved_args)


def _is_string_type(param_type: Any) -> bool:
    """Check if a parameter type is string or Optional[str].

    Args:
        param_type: Type annotation to check

    Returns:
        True if the type is string or Optional[str]
    """
    # TODO: refactor with isinstance
    return param_type == str or (  # noqa: E721
        hasattr(param_type, "__origin__")
        and param_type.__origin__ is Union
        and str in param_type.__args__
        and type(None) in param_type.__args__
    )


def _resolve_user_parameter(
    param_name: str,
    param_value: Any,
    param_type: Any,
) -> Any:
    """Handle _openeo_user parameter based on type annotation.

    Args:
        param_name: Name of the parameter
        param_value: Value to resolve
        param_type: Type annotation for the parameter

    Returns:
        Resolved parameter value
    """
    if param_name == "_openeo_user" and _is_string_type(param_type):
        return param_value.user_id
    return param_value


def _resolve_kwargs(
    kwargs: Dict[str, Any],
    named_parameters: Dict[str, Any],
    param_types: Dict[str, Any],
    func_name: str,
) -> Dict[str, Any]:
    """Resolve keyword arguments from parameter references.

    Args:
        kwargs: Dictionary of keyword arguments
        named_parameters: Dictionary of parameter values
        param_types: Dictionary of parameter type annotations
        func_name: Name of the function for error messages

    Returns:
        Dictionary of resolved keyword arguments

    Raises:
        ProcessParameterMissing: If a parameter reference cannot be resolved
    """
    resolved_kwargs = {}
    for k, arg in kwargs.items():
        if isinstance(arg, ParameterReference):
            if arg.from_parameter in named_parameters:
                value = named_parameters[arg.from_parameter]
                # Handle type-based parameter resolution
                if k in param_types:
                    value = _resolve_user_parameter(
                        arg.from_parameter, value, param_types[k]
                    )
                resolved_kwargs[k] = value
            else:
                raise ProcessParameterMissing(
                    f"Error: Process Parameter {arg.from_parameter} was missing for process {func_name}"
                )
        else:
            resolved_kwargs[k] = arg
    return resolved_kwargs


def _handle_special_args(
    resolved_kwargs: Dict[str, Any],
    func_signature: inspect.Signature,
) -> None:
    """Remove special arguments not in function signature.

    Args:
        resolved_kwargs: Dictionary of resolved keyword arguments
        func_signature: Function signature to check against
    """
    special_args = [
        "axis",  # Dimension to operate on
        "keepdims",  # Whether to preserve dimensions after reduction
        "context",  # Additional process context
        "dim_labels",  # Labels for dimensions
        "data",  # Input data reference
    ]
    for arg in special_args:
        if arg not in func_signature.parameters:
            resolved_kwargs.pop(arg, None)


def _is_array_like_union(non_none_types: list, args: tuple) -> str:
    """Check if a Union type represents an ArrayLike type.

    Args:
        non_none_types: Types in the Union excluding None
        args: All arguments in the Union including None

    Returns:
        Array type name if it's array-like, empty string otherwise
    """
    type_strs = [str(arg) for arg in non_none_types]

    # Count array-like indicators
    array_indicators = sum(
        1
        for ts in type_strs
        if "array" in ts.lower()
        or "sequence" in ts.lower()
        or "_SupportsArray" in ts
        or "ndarray" in ts
    )

    # Count basic primitive types that are part of array-like
    primitive_indicators = sum(
        1 for arg in non_none_types if arg in (bool, int, float, complex, str, bytes)
    )

    # If we have array indicators and/or multiple primitives, it's likely ArrayLike
    total_array_like = array_indicators + primitive_indicators
    if total_array_like >= len(non_none_types) * 0.7:  # 70% threshold
        # This is likely ArrayLike or similar
        if type(None) in args:
            return "array or null"
        return "array"

    return ""


def _handle_union_types(args: tuple) -> str:
    """Handle Union/Optional type annotations.

    Args:
        args: Arguments from get_args() of Union type

    Returns:
        Human-readable type name
    """
    # Filter out NoneType
    non_none_types = [arg for arg in args if arg is not type(None)]

    if len(non_none_types) == 1:
        # It's an Optional type
        base_name = _type_to_openeo_name(non_none_types[0])
        if type(None) in args:
            return f"{base_name} or null"
        return base_name

    # Check if this is a complex array-like union (like numpy's ArrayLike)
    array_like_result = _is_array_like_union(non_none_types, args)
    if array_like_result:
        return array_like_result

    # Multiple distinct types in Union
    type_names = [_type_to_openeo_name(arg) for arg in non_none_types]
    # Remove duplicates while preserving order
    seen = set()
    unique_names = []
    for name in type_names:
        if name not in seen:
            seen.add(name)
            unique_names.append(name)
    return " or ".join(unique_names)


def _type_to_openeo_name(param_type: Any) -> str:
    """Convert a Python type annotation to a human-readable OpenEO type name.

    Args:
        param_type: Python type annotation

    Returns:
        Human-readable type name for error messages
    """
    # Handle None type
    if param_type is type(None):
        return "null"

    # Handle ArrayLike and similar array types first (before Union handling)
    type_str = str(param_type)
    if "ArrayLike" in type_str or "ndarray" in type_str:
        return "array"

    # Handle Union/Optional types
    origin = get_origin(param_type)
    if origin is Union:
        args = get_args(param_type)
        return _handle_union_types(args)

    # Handle dict/RasterStack types
    if param_type is dict or (
        hasattr(param_type, "__origin__") and param_type.__origin__ is dict
    ):
        return "datacube"

    # Handle basic types
    if param_type is int:
        return "integer"
    if param_type is float:
        return "number"
    if param_type is str:
        return "string"
    if param_type is bool:
        return "boolean"

    # Handle custom types
    if hasattr(param_type, "__name__"):
        name = param_type.__name__
        if name == "LazyRasterStack":
            return "datacube"
        if name == "RasterStack":
            return "datacube"
        return name

    # Fallback to string representation
    return str(param_type)


def _value_to_openeo_name(value: Any) -> str:
    """Convert a value's type to a human-readable OpenEO type name.

    Args:
        value: The value whose type to describe

    Returns:
        Human-readable type name
    """

    if value is None:
        return "null"

    value_type = type(value)

    if isinstance(value, dict):
        return "datacube"
    if isinstance(value, LazyRasterStack):
        return "datacube"
    if hasattr(value, "__array__"):
        return "array"
    if value_type is int:
        return "integer"
    if value_type is float:
        return "number"
    if value_type is str:
        return "string"
    if value_type is bool:
        return "boolean"

    return value_type.__name__


def _validate_parameter_types(
    resolved_kwargs: Dict[str, Any],
    param_types: Dict[str, Any],
    func_name: str,
) -> None:
    """Validate parameter types using Pydantic.

    Args:
        resolved_kwargs: Dictionary of resolved keyword arguments
        param_types: Dictionary of parameter type annotations
        func_name: Name of the function for error messages

    Raises:
        TypeError: If a parameter has an invalid type
    """

    for param_name, param_value in resolved_kwargs.items():
        if param_name not in param_types:
            continue

        param_type = param_types[param_name]

        # Skip validation for parameters without type annotations or with Any type
        if param_type is inspect.Parameter.empty or param_type is Any:
            continue

        # Handle None values for Optional types
        if param_value is None:
            origin = get_origin(param_type)
            if origin is Union:
                args = get_args(param_type)
                if type(None) in args:
                    # None is allowed for Optional types
                    continue
            # If we get here, None is not allowed
            raise TypeError(
                f"Parameter '{param_name}' in process '{func_name}' cannot be None"
            )

        # Check for dict/RasterStack being passed to ArrayLike parameters
        # This is a common mistake we want to catch
        # Note: RasterStack is just Dict[str, ImageData], so we check isinstance(dict)
        if isinstance(param_value, (dict, LazyRasterStack)):
            # Check if the expected type is array-like (not a dict/RasterStack)
            origin = get_origin(param_type)

            # Handle Optional types
            if origin is Union:
                args = get_args(param_type)
                # Filter out NoneType to get the actual types
                actual_types = [arg for arg in args if arg is not type(None)]

                # Check if any of the actual types are dict-based
                is_dict_expected = any(
                    arg is dict
                    or (hasattr(arg, "__origin__") and arg.__origin__ is dict)
                    for arg in actual_types
                )
            else:
                is_dict_expected = param_type is dict or (
                    hasattr(param_type, "__origin__") and param_type.__origin__ is dict
                )

            if not is_dict_expected:
                expected_type_name = _type_to_openeo_name(param_type)
                actual_type_name = _value_to_openeo_name(param_value)
                raise TypeError(
                    f"Parameter '{param_name}' in process '{func_name}': "
                    f"expected '{expected_type_name}' but got '{actual_type_name}'"
                )

        # Use Pydantic TypeAdapter for general validation
        try:
            adapter = TypeAdapter(param_type)
            # Validate the value - this will raise ValidationError if invalid
            adapter.validate_python(param_value)
        except ValidationError as e:
            expected_type_name = _type_to_openeo_name(param_type)
            actual_type_name = _value_to_openeo_name(param_value)
            raise TypeError(
                f"Parameter '{param_name}' in process '{func_name}': "
                f"expected '{expected_type_name}' but got '{actual_type_name}'. "
                f"Details: {e}"
            ) from e
        except Exception:
            # Skip validation if TypeAdapter can't handle the type
            # (e.g., for complex custom types that Pydantic doesn't understand)
            logger.debug(
                f"Could not validate type for parameter '{param_name}' with type '{param_type}'"
            )


def process(f):
    """Handle parameter resolution in the OpenEO processing pipeline.

    This decorator resolves parameter references to their actual values,
    handles type-based parameter extraction (like user_id from User objects),
    and manages special OpenEO parameters.

    Args:
        f: Function to decorate

    Returns:
        Wrapped function that handles parameter resolution

    Example:
        @process
        def compute_ndvi(red: float, nir: float) -> float:
            return (nir - red) / (nir + red)
    """

    @wraps(f)
    def wrapper(
        *args,
        positional_parameters: Optional[Dict[str, int]] = None,
        named_parameters: Optional[Dict[str, Any]] = None,
        **kwargs,
    ):
        # Initialize parameter dictionaries
        args_list = list(args)
        if positional_parameters is None:
            positional_parameters = {}
        if named_parameters is None:
            named_parameters = {}

        # Handle parameter resolution
        _handle_positional_parameters(args, positional_parameters, named_parameters)
        resolved_args = _resolve_positional_args(
            tuple(args_list), named_parameters, f.__name__
        )

        # Get parameter types from function signature
        sig = inspect.signature(f)
        param_types = {name: param.annotation for name, param in sig.parameters.items()}

        # Resolve keyword arguments
        resolved_kwargs = _resolve_kwargs(
            kwargs, named_parameters, param_types, f.__name__
        )

        # Handle special parameters
        _handle_special_args(resolved_kwargs, sig)

        # Validate parameter types
        _validate_parameter_types(resolved_kwargs, param_types, f.__name__)

        # Debug logging
        pretty_args = {k: repr(v)[:80] for k, v in resolved_kwargs.items()}
        if hasattr(f, "__name__"):
            logger.debug(
                f"Running process {f.__name__} with resolved parameters: {pretty_args}"
            )

        return f(*resolved_args, **resolved_kwargs)

    return wrapper
