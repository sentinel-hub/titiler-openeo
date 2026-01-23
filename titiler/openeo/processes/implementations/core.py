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

# Arguments that may appear in named_parameters but should be removed
# if not explicitly in the function signature
_SPECIAL_OPENEO_ARGS = frozenset(["axis", "keepdims", "context", "dim_labels", "data"])


def _handle_positional_parameters(
    args: Tuple[Any, ...],
    positional_parameters: Dict[str, int],
    named_parameters: Dict[str, Any],
) -> None:
    """Map positional parameters to named parameters (idempotent).

    When the @process decorator calls a function that is also decorated with @process,
    this function may be called multiple times. We preserve already-resolved values
    and don't overwrite them with ParameterReference objects from inner decorator calls.

    Args:
        args: Tuple of positional arguments from function call
        positional_parameters: Maps parameter names to argument positions
        named_parameters: Dictionary to store mapped parameters (modified in place)
    """
    for arg_name, i in positional_parameters.items():
        existing = named_parameters.get(arg_name)
        # Keep existing value if it's already resolved (not a ParameterReference)
        if existing is not None and not isinstance(existing, ParameterReference):
            continue
        named_parameters[arg_name] = args[i]


def _resolve_positional_args(
    args: Tuple[Any, ...],
    named_parameters: Dict[str, Any],
    func_name: str,
) -> Tuple[Any, ...]:
    """Resolve ParameterReference objects in positional arguments.

    Args:
        args: Tuple of positional arguments
        named_parameters: Dictionary of parameter values
        func_name: Name of the function for error messages

    Returns:
        Tuple of resolved arguments (only those that were ParameterReferences)

    Raises:
        ProcessParameterMissing: If a parameter reference cannot be resolved
    """
    resolved = []
    for arg in args:
        if isinstance(arg, ParameterReference):
            if arg.from_parameter not in named_parameters:
                raise ProcessParameterMissing(
                    f"Parameter '{arg.from_parameter}' missing for process '{func_name}'"
                )
            resolved.append(named_parameters[arg.from_parameter])
    return tuple(resolved)


def _is_optional_type(param_type: Any) -> Tuple[bool, Any]:
    """Check if a type is Optional[T] and extract the underlying type.

    Args:
        param_type: Type annotation to check

    Returns:
        (is_optional, underlying_type): If optional, underlying_type is the non-None type
    """
    if get_origin(param_type) is not Union:
        return False, param_type

    args = get_args(param_type)
    if type(None) not in args:
        return False, param_type

    non_none = [a for a in args if a is not type(None)]
    if len(non_none) == 1:
        return True, non_none[0]
    if len(non_none) > 1:
        return True, Union[tuple(non_none)]
    return True, type(None)


def _is_string_type(param_type: Any) -> bool:
    """Check if a type is str or Optional[str]."""
    _, underlying = _is_optional_type(param_type)
    return underlying is str


def _convert_to_bounding_box(value: Any) -> BoundingBox:
    """Convert a dict to BoundingBox if needed."""
    if isinstance(value, BoundingBox):
        return value
    if isinstance(value, dict):
        return BoundingBox(
            west=value.get("west"),
            east=value.get("east"),
            south=value.get("south"),
            north=value.get("north"),
            crs=value.get("crs"),
        )
    return value


def _convert_to_temporal_interval(value: Any) -> TemporalInterval:
    """Convert a dict or list to TemporalInterval if needed."""
    if isinstance(value, TemporalInterval):
        return value
    if isinstance(value, dict):
        return TemporalInterval([value.get("start"), value.get("end")])
    if isinstance(value, list) and len(value) == 2:
        return TemporalInterval(value)
    return value


def _resolve_special_parameter(
    param_name: str,
    param_value: Any,
    param_type: Any,
) -> Any:
    """Handle special parameters based on type annotation.

    Transforms:
    - User objects to user_id string (when param_name is _openeo_user and type is str)
    - Dicts to BoundingBox or TemporalInterval objects
    """
    # Extract user_id from User objects when string type expected
    if param_name == "_openeo_user" and _is_string_type(param_type):
        return param_value.user_id

    _, effective_type = _is_optional_type(param_type)

    if effective_type == BoundingBox:
        return _convert_to_bounding_box(param_value)
    if effective_type == TemporalInterval:
        return _convert_to_temporal_interval(param_value)

    return param_value


def _resolve_kwargs(
    kwargs: Dict[str, Any],
    named_parameters: Dict[str, Any],
    param_types: Dict[str, Any],
    func_name: str,
) -> Dict[str, Any]:
    """Resolve ParameterReference objects in keyword arguments.

    All ParameterReference objects are resolved from named_parameters.
    Special parameter transformations (User->user_id, dict->BoundingBox) are applied.

    Raises:
        ProcessParameterMissing: If a parameter reference cannot be resolved
    """
    resolved = {}
    for key, value in kwargs.items():
        if not isinstance(value, ParameterReference):
            resolved[key] = value
            continue

        ref_name = value.from_parameter
        if ref_name not in named_parameters:
            raise ProcessParameterMissing(
                f"Parameter '{ref_name}' missing for process '{func_name}'"
            )

        resolved_value = named_parameters[ref_name]
        if key in param_types:
            resolved_value = _resolve_special_parameter(
                ref_name, resolved_value, param_types[key]
            )
        resolved[key] = resolved_value

    return resolved


def _handle_special_args(
    resolved_kwargs: Dict[str, Any],
    func_signature: inspect.Signature,
) -> None:
    """Remove OpenEO special arguments not in function signature."""
    for arg in _SPECIAL_OPENEO_ARGS:
        if arg not in func_signature.parameters:
            resolved_kwargs.pop(arg, None)


def _is_array_like_union(non_none_types: list, args: tuple) -> str:
    """Check if a Union type represents an ArrayLike type.

    Returns "array" or "array or null" if array-like, empty string otherwise.
    Uses heuristic: 70%+ of types are array indicators or primitives.
    """
    type_strs = [str(t) for t in non_none_types]
    array_keywords = ("array", "sequence", "_SupportsArray", "ndarray")
    primitives = (bool, int, float, complex, str, bytes)

    array_count = sum(
        1 for ts in type_strs if any(k in ts.lower() for k in array_keywords)
    )
    primitive_count = sum(1 for t in non_none_types if t in primitives)
    total = array_count + primitive_count

    if total >= len(non_none_types) * 0.7:
        return "array or null" if type(None) in args else "array"
    return ""


def _handle_union_types(args: tuple) -> str:
    """Convert Union type to human-readable OpenEO name."""
    non_none = [a for a in args if a is not type(None)]

    if len(non_none) == 1:
        base = _type_to_openeo_name(non_none[0])
        return f"{base} or null" if type(None) in args else base

    # Check for array-like union
    result = _is_array_like_union(non_none, args)
    if result:
        return result

    # Multiple distinct types - deduplicate names
    names = [_type_to_openeo_name(t) for t in non_none]
    unique = list(dict.fromkeys(names))  # Preserves order, removes duplicates
    return " or ".join(unique)


# Mapping of Python types to OpenEO type names
_TYPE_TO_OPENEO = {
    int: "integer",
    float: "number",
    str: "string",
    bool: "boolean",
    dict: "datacube",
    type(None): "null",
}


def _type_to_openeo_name(param_type: Any) -> str:
    """Convert a Python type annotation to OpenEO type name for error messages."""
    # Handle None type
    if param_type is type(None):
        return "null"

    # Handle ArrayLike before Union (string check)
    type_str = str(param_type)
    if "ArrayLike" in type_str or "ndarray" in type_str:
        return "array"

    # Handle Union/Optional
    if get_origin(param_type) is Union:
        return _handle_union_types(get_args(param_type))

    # Handle dict types
    if param_type is dict or (
        hasattr(param_type, "__origin__") and param_type.__origin__ is dict
    ):
        return "datacube"

    # Handle basic types
    if param_type in _TYPE_TO_OPENEO:
        return _TYPE_TO_OPENEO[param_type]

    # Handle custom types by name
    if hasattr(param_type, "__name__"):
        name = param_type.__name__
        if name in ("LazyRasterStack", "RasterStack"):
            return "datacube"
        return name

    return str(param_type)


def _value_to_openeo_name(value: Any) -> str:
    """Convert a value's type to OpenEO type name for error messages."""
    if value is None:
        return "null"

    # Handle OpenEO schema types first
    if isinstance(value, BoundingBox):
        return "bounding-box"
    if isinstance(value, TemporalInterval):
        return "temporal-interval"
    if isinstance(value, (dict, LazyRasterStack)):
        return "datacube"
    if hasattr(value, "__array__"):
        return "array"

    return _TYPE_TO_OPENEO.get(type(value), type(value).__name__)


def _is_dict_type_expected(param_type: Any) -> bool:
    """Check if the parameter type expects a dict/datacube."""
    origin = get_origin(param_type)

    if origin is Union:
        actual_types = [a for a in get_args(param_type) if a is not type(None)]
        return any(
            t is dict or (hasattr(t, "__origin__") and t.__origin__ is dict)
            for t in actual_types
        )

    return param_type is dict or (
        hasattr(param_type, "__origin__") and param_type.__origin__ is dict
    )


def _validate_datacube_param(
    param_name: str, param_value: Any, param_type: Any, func_name: str
) -> None:
    """Validate that datacube values are expected by the parameter type."""
    if not isinstance(param_value, (dict, LazyRasterStack)):
        return

    if not _is_dict_type_expected(param_type):
        raise TypeError(
            f"Parameter '{param_name}' in process '{func_name}': "
            f"expected '{_type_to_openeo_name(param_type)}' but got '{_value_to_openeo_name(param_value)}'"
        )


def _validate_basemodel_param(param_value: Any, param_type: Any) -> bool:
    """Check if BaseModel value matches expected type. Returns True if valid/skip."""
    if not isinstance(param_value, BaseModel):
        return False

    _, underlying = _is_optional_type(param_type)
    origin = get_origin(underlying)
    check_type = origin if origin is not None else underlying

    try:
        if isinstance(check_type, type) and isinstance(param_value, check_type):
            return True
    except TypeError:
        pass
    return False


def _validate_with_pydantic(
    param_name: str, param_value: Any, param_type: Any, func_name: str
) -> None:
    """Validate parameter using Pydantic TypeAdapter."""
    try:
        TypeAdapter(param_type).validate_python(param_value)
    except ValidationError as e:
        raise TypeError(
            f"Parameter '{param_name}' in process '{func_name}': "
            f"expected '{_type_to_openeo_name(param_type)}' but got '{_value_to_openeo_name(param_value)}'. "
            f"Details: {e}"
        ) from e
    except Exception:
        logger.debug(f"Could not validate '{param_name}' with type '{param_type}'")


def _validate_parameter_types(
    resolved_kwargs: Dict[str, Any],
    param_types: Dict[str, Any],
    func_name: str,
) -> None:
    """Validate parameter types using Pydantic.

    Catches common type mismatches:
    - None values for non-Optional types
    - Datacubes (dict/LazyRasterStack) passed to array parameters
    - General type mismatches via Pydantic validation
    """
    for param_name, param_value in resolved_kwargs.items():
        param_type = param_types.get(param_name)
        if (
            param_type is None
            or param_type is inspect.Parameter.empty
            or param_type is Any
        ):
            continue

        # Handle None values
        if param_value is None:
            is_optional, _ = _is_optional_type(param_type)
            if not is_optional:
                raise TypeError(
                    f"Parameter '{param_name}' in process '{func_name}' cannot be None"
                )
            continue

        # Check datacube/dict type mismatches
        _validate_datacube_param(param_name, param_value, param_type, func_name)

        # Skip already-validated BaseModel instances
        if _validate_basemodel_param(param_value, param_type):
            continue

        # General Pydantic validation
        _validate_with_pydantic(param_name, param_value, param_type, func_name)


def _auto_map_positional_parameters(
    args: Tuple[Any, ...],
    sig: inspect.Signature,
) -> Dict[str, int]:
    """Create positional_parameters mapping from function signature for regular Python calls."""
    if not args:
        return {}

    param_names = [
        p
        for p in sig.parameters.keys()
        if p not in ("positional_parameters", "named_parameters")
    ]
    return {param_names[i]: i for i in range(min(len(args), len(param_names)))}


def _resolve_parameter_reference(
    param_name: str,
    value: ParameterReference,
    named_parameters: Dict[str, Any],
    param_types: Dict[str, Any],
    func_name: str,
) -> Optional[Any]:
    """Resolve a single ParameterReference. Returns None if circular reference detected."""
    ref_param = value.from_parameter

    if ref_param not in named_parameters:
        raise ProcessParameterMissing(
            f"Parameter '{ref_param}' missing for process '{func_name}'"
        )

    resolved = named_parameters[ref_param]

    # Circular reference check
    if isinstance(resolved, ParameterReference):
        logger.warning(
            f"Parameter {param_name} references {ref_param} which is also a ParameterReference"
        )
        return None

    # Apply special parameter transformations
    param_type = param_types.get(param_name)
    if param_type and param_type != inspect.Parameter.empty:
        resolved = _resolve_special_parameter(ref_param, resolved, param_type)

    return resolved


def _extract_remaining_parameters(
    sig: inspect.Signature,
    resolved_kwargs: Dict[str, Any],
    named_parameters: Dict[str, Any],
    param_types: Dict[str, Any],
    func_name: str,
) -> None:
    """Extract parameters from named_parameters that weren't in kwargs (modifies resolved_kwargs)."""
    skip_params = {"positional_parameters", "named_parameters"}

    for param_name in sig.parameters:
        if param_name in resolved_kwargs or param_name in skip_params:
            continue

        if param_name not in named_parameters:
            continue

        value = named_parameters[param_name]

        if isinstance(value, ParameterReference):
            resolved = _resolve_parameter_reference(
                param_name, value, named_parameters, param_types, func_name
            )
            if resolved is not None:
                resolved_kwargs[param_name] = resolved
        else:
            resolved_kwargs[param_name] = value


def process(f):
    """Handle parameter resolution in the OpenEO processing pipeline.

    This decorator resolves OpenEO ParameterReference objects and handles two calling patterns:

    1. OpenEO Process Graph Calls:
       function(positional_parameters={"x": 0}, named_parameters={"x": value})

    2. Regular Python Calls:
       function(x, y, z) or function(x=1, y=2)

    The decorator is idempotent - nested @process calls won't re-resolve already-resolved values.
    """

    @wraps(f)
    def wrapper(
        *args,
        positional_parameters: Optional[Dict[str, int]] = None,
        named_parameters: Optional[Dict[str, Any]] = None,
        **kwargs,
    ):
        # Initialize defaults
        positional_parameters = positional_parameters or {}
        named_parameters = named_parameters or {}

        sig = inspect.signature(f)
        param_types = {name: p.annotation for name, p in sig.parameters.items()}

        # Auto-map positional args for regular Python calls
        if args and not positional_parameters:
            positional_parameters = _auto_map_positional_parameters(args, sig)

        # Map positional args to named_parameters (idempotent)
        _handle_positional_parameters(args, positional_parameters, named_parameters)

        # Resolve ParameterReferences in kwargs
        resolved_kwargs = _resolve_kwargs(
            kwargs, named_parameters, param_types, f.__name__
        )

        # Extract remaining parameters from named_parameters
        _extract_remaining_parameters(
            sig, resolved_kwargs, named_parameters, param_types, f.__name__
        )

        # Remove OpenEO special args not in signature
        _handle_special_args(resolved_kwargs, sig)

        # Pass named_parameters if function expects it
        if "named_parameters" in sig.parameters:
            resolved_kwargs["named_parameters"] = named_parameters

        # Validate parameter types
        _validate_parameter_types(resolved_kwargs, param_types, f.__name__)

        logger.debug(f"Running {f.__name__} with: {list(resolved_kwargs.keys())}")

        return f(**resolved_kwargs)

    return wrapper
