"""Process decorator for OpenEO parameter resolution.

This module provides the @process decorator which is the core mechanism for resolving
OpenEO process graph parameters. The decorator handles:
1. ParameterReference resolution (converting references to actual values)
2. Multiple parameter sources (args, kwargs, positional_parameters, named_parameters)
3. Type-based special parameter handling (User -> user_id, dict -> BoundingBox, etc.)
4. Type validation using Pydantic
5. Idempotent operation (safe for recursive/nested process calls)

Key Design Decisions:
- Removed "special_args" list to ensure ALL ParameterReference objects get resolved
- Made parameter handling idempotent to support recursive decorator calls
- Simplified parameter flow: all parameters end up in resolved_kwargs
- Support both OpenEO graph calls (with positional_parameters/named_parameters dicts)
  and regular Python function calls (with positional args)
"""

import inspect
import logging
from functools import wraps
from typing import Any, Dict, Optional, Tuple, Union, get_args, get_origin

from openeo_pg_parser_networkx.pg_schema import (
    BoundingBox,
    ParameterReference,
    TemporalInterval,
)
from pydantic import BaseModel, TypeAdapter, ValidationError

from ...errors import ProcessParameterMissing
from .data_model import LazyRasterStack

logger = logging.getLogger(__name__)


def _handle_positional_parameters(
    args: Tuple[Any, ...],
    positional_parameters: Dict[str, int],
    named_parameters: Dict[str, Any],
) -> None:
    """Map positional parameters to named parameters.

    This function is IDEMPOTENT - critical for recursive decorator calls.
    When the @process decorator calls a function that is also decorated with @process,
    this function may be called multiple times with the same parameters. We must
    preserve already-resolved values and not overwrite them with ParameterReference
    objects from inner decorator calls.

    Args:
        args: Tuple of positional arguments from function call
        positional_parameters: Maps parameter names to argument positions
                             e.g., {"data": 0, "value": 1}
        named_parameters: Dictionary to store mapped parameters (modified in place)

    Example:
        # OpenEO graph call:
        array_element(positional_parameters={"data": 0, "index": 1},
                     named_parameters={"data": <some_value>, "index": 1})
        # This function maps args[0] -> named_parameters["data"] if not already resolved
    """
    for arg_name, i in positional_parameters.items():
        # Check if named_parameters already has a resolved value
        existing_value = named_parameters.get(arg_name)

        # If existing value exists and is NOT a ParameterReference, keep it (it's already resolved)
        if existing_value is not None and not isinstance(
            existing_value, ParameterReference
        ):
            continue

        # Otherwise, map the positional arg to named_parameters
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


def _is_optional_type(param_type: Any) -> Tuple[bool, Any]:
    """Check if a parameter type is Optional and return the underlying type.

    Args:
        param_type: Type annotation to check

    Returns:
        Tuple of (is_optional: bool, underlying_type: Any)
        If optional, underlying_type is the non-None type
    """
    origin = get_origin(param_type)
    if origin is Union:
        args = get_args(param_type)
        if type(None) in args:
            # Filter out NoneType to get the actual type
            non_none_types = [arg for arg in args if arg is not type(None)]
            if len(non_none_types) == 1:
                return True, non_none_types[0]
            elif len(non_none_types) > 1:
                # Multiple non-None types, reconstruct Union without None
                return True, Union[tuple(non_none_types)]
    return False, param_type


def _is_string_type(param_type: Any) -> bool:
    """Check if a parameter type is string or Optional[str].

    Args:
        param_type: Type annotation to check

    Returns:
        True if the type is string or Optional[str]
    """
    # Use the new helper function
    is_optional, underlying_type = _is_optional_type(param_type)
    return underlying_type is str


def _resolve_special_parameter(
    param_name: str,
    param_value: Any,
    param_type: Any,
) -> Any:
    """Handle special parameters based on type annotation.

    Args:
        param_name: Name of the parameter
        param_value: Value to resolve
        param_type: Type annotation for the parameter

    Returns:
        Resolved parameter value
    """
    if param_name == "_openeo_user" and _is_string_type(param_type):
        return param_value.user_id

    # Check for BoundingBox (including Optional[BoundingBox])
    is_optional, underlying_type = _is_optional_type(param_type)
    effective_type = underlying_type if is_optional else param_type

    if effective_type == BoundingBox:
        # If already a BoundingBox, return as-is
        if isinstance(param_value, BoundingBox):
            return param_value
        if isinstance(param_value, dict):
            return BoundingBox(
                west=param_value.get("west"),
                east=param_value.get("east"),
                south=param_value.get("south"),
                north=param_value.get("north"),
                crs=param_value.get("crs", None),
            )
    if effective_type == TemporalInterval:
        # If already a TemporalInterval, return as-is
        if isinstance(param_value, TemporalInterval):
            return param_value
        if isinstance(param_value, dict):
            return TemporalInterval(
                [param_value.get("start", None), param_value.get("end", None)]
            )
        elif isinstance(param_value, list) and len(param_value) == 2:
            return TemporalInterval(param_value)

    return param_value


def _resolve_kwargs(
    kwargs: Dict[str, Any],
    named_parameters: Dict[str, Any],
    param_types: Dict[str, Any],
    func_name: str,
) -> Dict[str, Any]:
    """Resolve keyword arguments from parameter references.

    This function handles ParameterReference objects in kwargs, which are placeholders
    that reference values in named_parameters. For example:
    - kwargs["data"] = ParameterReference(from_parameter="data")
    - named_parameters["data"] = <actual_array>
    - Result: resolved_kwargs["data"] = <actual_array>

    IMPORTANT: This function resolves ALL ParameterReference objects, including "data".
    Previously, there was a "special_args" list that prevented resolution of certain
    parameters, but this caused bugs where ParameterReference objects reached functions
    unresolved. That list has been removed.

    Args:
        kwargs: Dictionary of keyword arguments (may contain ParameterReference)
        named_parameters: Dictionary mapping parameter names to their actual values
        param_types: Dictionary of parameter type annotations for special handling
        func_name: Name of the function for error messages

    Returns:
        Dictionary of resolved keyword arguments (no ParameterReference objects)

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
                    try:
                        value = _resolve_special_parameter(
                            arg.from_parameter, value, param_types[k]
                        )
                    except Exception as e:
                        raise ProcessParameterMissing(
                            f"Error resolving parameter {arg.from_parameter} for process {func_name}: {e}"
                        ) from e
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

    # Handle OpenEO schema types first (before dict check)
    if isinstance(value, BoundingBox):
        return "bounding-box"
    if isinstance(value, TemporalInterval):
        return "temporal-interval"

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

    This function performs runtime type validation on resolved parameters using
    Pydantic's TypeAdapter. It catches common type mismatches that would otherwise
    cause cryptic errors deeper in the execution:

    Common validations:
    - Ensuring datacubes (dict/LazyRasterStack) aren't passed to array parameters
    - Checking None values are only used with Optional types
    - Validating subscripted generics (e.g., Optional[BoundingBox])

    NOTE: Uses get_origin() to extract base classes before isinstance() checks
    to avoid "TypeError: Subscripted generics cannot be used with class and instance checks"

    Args:
        resolved_kwargs: Dictionary of resolved keyword arguments
        param_types: Dictionary of parameter type annotations
        func_name: Name of the function for error messages

    Raises:
        TypeError: If a parameter has an invalid type, with human-readable error message
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
            is_optional, underlying_type = _is_optional_type(param_type)
            if is_optional:
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

        # Skip validation for already-resolved Pydantic BaseModel instances
        # These are already validated when constructed (e.g., BoundingBox, TemporalInterval)
        if isinstance(param_value, BaseModel):
            # Check if the type matches the expected type
            is_opt, underlying = _is_optional_type(param_type)
            expected_type = underlying if is_opt else param_type

            # Get the origin type for isinstance checks (handles subscripted generics)
            origin_type = get_origin(expected_type)
            check_type = origin_type if origin_type is not None else expected_type

            # For subscripted generics, we need to check if it's a class before using isinstance
            try:
                if isinstance(check_type, type) and isinstance(param_value, check_type):
                    continue
            except TypeError:
                # If isinstance fails (e.g., for complex types), skip the check
                # and let Pydantic validation handle it below
                pass

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

    ARCHITECTURE OVERVIEW:
    This decorator is the central mechanism for OpenEO parameter resolution. It handles
    two distinct calling patterns:

    1. OpenEO Process Graph Calls:
       function(positional_parameters={"x": 0}, named_parameters={"x": value, ...})
       - positional_parameters maps parameter names to arg positions
       - named_parameters contains the actual parameter values
       - May contain ParameterReference objects that need resolution

    2. Regular Python Calls:
       function(x, y, z) or function(x=1, y=2)
       - Standard positional/keyword arguments
       - Auto-mapped to named_parameters for consistency

    PARAMETER RESOLUTION FLOW:
    1. Auto-create positional_parameters mapping if args provided without mapping
    2. Map positional args to named_parameters using _handle_positional_parameters
    3. Resolve ParameterReference objects in kwargs using _resolve_kwargs
    4. Extract and resolve any remaining ParameterReference in named_parameters
    5. Apply special parameter transformations (User -> user_id, dict -> BoundingBox)
    6. Remove parameters not in function signature
    7. Validate parameter types
    8. Call function with **resolved_kwargs only (no positional args)

    IDEMPOTENCY:
    The decorator is idempotent - if a @process decorated function calls another
    @process decorated function, parameters that are already resolved won't be
    overwritten. This is crucial for recursive/nested process calls.

    DESIGN DECISION - Why remove "special_args"?
    Previously, a "special_args" list prevented resolution of parameters like "data".
    This caused bugs where ParameterReference(from_parameter="data") objects reached
    functions unresolved, causing errors. All parameters must be resolved.

    Args:
        f: Function to decorate (typically an OpenEO process implementation)

    Returns:
        Wrapped function that handles parameter resolution transparently

    Example:
        @process
        def array_element(data: list, index: int) -> Any:
            return data[index]

        # Both calling patterns work:
        array_element([1, 2, 3], 1)  # Regular Python call -> 2
        array_element(
            positional_parameters={"data": 0, "index": 1},
            named_parameters={"data": [1, 2, 3], "index": 1}
        )  # OpenEO graph call -> 2
    """

    @wraps(f)
    def wrapper(
        *args,
        positional_parameters: Optional[Dict[str, int]] = None,
        named_parameters: Optional[Dict[str, Any]] = None,
        **kwargs,
    ):
        # Initialize parameter dictionaries
        # These may be None for regular Python calls, so provide defaults
        if positional_parameters is None:
            positional_parameters = {}
        if named_parameters is None:
            named_parameters = {}

        # Get parameter types from function signature for type validation
        sig = inspect.signature(f)
        param_types = {name: param.annotation for name, param in sig.parameters.items()}

        # AUTO-MAPPING FOR REGULAR PYTHON CALLS:
        # If args are provided but no positional_parameters mapping exists,
        # this is a regular Python function call (not an OpenEO graph call).
        # Create the positional_parameters mapping based on function signature.
        # Example: array_element([1,2,3], 1) -> positional_parameters={"data": 0, "index": 1}
        if args and not positional_parameters:
            # Get parameter names, excluding the special decorator parameters
            param_names = [
                p
                for p in sig.parameters.keys()
                if p not in ("positional_parameters", "named_parameters")
            ]
            # Create mapping: parameter_name -> argument_index
            for i, _ in enumerate(args):
                if i < len(param_names):
                    positional_parameters[param_names[i]] = i

        # STEP 1: Map positional args to named_parameters
        # Uses positional_parameters mapping to know which arg goes to which parameter
        # Idempotent: won't overwrite already-resolved values
        _handle_positional_parameters(args, positional_parameters, named_parameters)

        # STEP 2: Resolve all kwargs (handles ParameterReference objects)
        # Any ParameterReference in kwargs gets resolved from named_parameters
        # This also applies special parameter transformations
        resolved_kwargs = _resolve_kwargs(
            kwargs, named_parameters, param_types, f.__name__
        )

        # STEP 3: Extract and resolve remaining parameters from named_parameters
        # These are parameters that:
        # - Match the function signature
        # - Weren't already in kwargs
        # - May still contain ParameterReference objects that need resolution
        for param_name in sig.parameters:
            # Skip if already in resolved_kwargs or special parameters
            if param_name in resolved_kwargs or param_name in (
                "positional_parameters",
                "named_parameters",
            ):
                continue

            if param_name in named_parameters:
                value = named_parameters[param_name]

                # If it's a ParameterReference, resolve it
                if isinstance(value, ParameterReference):
                    # Look up the referenced parameter
                    ref_param = value.from_parameter
                    if ref_param in named_parameters:
                        resolved_value = named_parameters[ref_param]
                        # CIRCULAR REFERENCE CHECK:
                        # If the resolved value is also a ParameterReference, it means
                        # we're in a self-reference situation (e.g., data -> data)
                        # This indicates the parameter hasn't been properly resolved yet.
                        # Skip it to avoid infinite loops.
                        if isinstance(resolved_value, ParameterReference):
                            logger.warning(
                                f"Parameter {param_name} references {ref_param} which is also a ParameterReference. "
                                f"This suggests unresolved parameters in the process graph."
                            )
                            continue

                        # Apply special parameter resolution if needed
                        if (
                            param_name in param_types
                            and param_types[param_name] != inspect.Parameter.empty
                        ):
                            try:
                                resolved_value = _resolve_special_parameter(
                                    ref_param, resolved_value, param_types[param_name]
                                )
                            except Exception as e:
                                raise ProcessParameterMissing(
                                    f"Error resolving parameter {ref_param} for process {f.__name__}: {e}"
                                ) from e
                        resolved_kwargs[param_name] = resolved_value
                    else:
                        raise ProcessParameterMissing(
                            f"Error: Process Parameter {ref_param} was missing for process {f.__name__}"
                        )
                else:
                    # Already resolved, add directly
                    resolved_kwargs[param_name] = value

        # STEP 4: Remove parameters not in function signature
        # Some parameters (like "axis", "context") may be present in named_parameters
        # but not expected by the function. Remove them to avoid TypeError.
        _handle_special_args(resolved_kwargs, sig)

        # STEP 5: Pass named_parameters if function explicitly expects it
        # Some functions need access to the full named_parameters dict for context
        if "named_parameters" in sig.parameters:
            resolved_kwargs["named_parameters"] = named_parameters

        # STEP 6: Validate all parameter types using Pydantic
        # Catches type mismatches before they cause cryptic errors
        _validate_parameter_types(resolved_kwargs, param_types, f.__name__)

        # Debug logging (truncate values to avoid huge logs)
        pretty_args = {k: repr(v)[:80] for k, v in resolved_kwargs.items()}
        if hasattr(f, "__name__"):
            logger.debug(
                f"Running process {f.__name__} with resolved parameters: {pretty_args}"
            )

        # STEP 7: Call the wrapped function with resolved kwargs ONLY
        # We don't use positional args here - everything is passed as keyword arguments.
        # This ensures consistent parameter passing regardless of calling pattern.
        return f(**resolved_kwargs)

    return wrapper
