"""Process decorator for OpenEO parameter resolution."""

import inspect
import logging
from functools import wraps
from typing import Any, Dict, Optional, Tuple, Union

from openeo_pg_parser_networkx.pg_schema import ParameterReference

from ...errors import ProcessParameterMissing

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
    return param_type == str or (
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

        # Debug logging
        pretty_args = {k: repr(v)[:80] for k, v in resolved_kwargs.items()}
        if hasattr(f, "__name__"):
            logger.debug(
                f"Running process {f.__name__} with resolved parameters: {pretty_args}"
            )

        return f(*resolved_args, **resolved_kwargs)

    return wrapper
