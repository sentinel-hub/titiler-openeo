from typing import Any
from jsonpath_ng import parse
from jsonpath_ng.exceptions import JsonPathParserError

from ...errors import ProcessParameterInvalid, ProcessParameterMissing

__all__ = [
    "get_param_item",
]

def _validate_structure(obj: Any, path: str = "$") -> None:
    """
    Validate that object only contains dicts and lists
    """
    if isinstance(obj, dict):
        for key, value in obj.items():
            if not isinstance(key, str):
                raise ProcessParameterInvalid(
                    f"Invalid key type at {path}: only string keys are allowed"
                )
            _validate_structure(value, f"{path}.{key}")
    elif isinstance(obj, list):
        for i, value in enumerate(obj):
            _validate_structure(value, f"{path}[{i}]")
    elif not isinstance(obj, (str, int, float, bool, type(None))):
        raise ProcessParameterInvalid(
            f"Invalid type at {path}: only dict, list, and JSON-compatible scalar types are allowed"
        )

def get_param_item(parameter: Any, path: str) -> Any:
    """Get a value from a parameter using JSONPath syntax"""
    # First validate the parameter structure
    try:
        _validate_structure(parameter)
    except ProcessParameterInvalid as e:
        # Keep original validation error messages
        raise e

    # Handle empty structures
    if isinstance(parameter, dict) and not parameter:
        return None
    
    # Handle empty array access
    if isinstance(parameter, dict) and any(isinstance(v, list) and not v for v in parameter.values()):
        try:
            parent_path = path.split('[')[0]
            array_value = parameter
            for part in parent_path.strip('$').strip('.').split('.'):
                if part:
                    array_value = array_value[part]
            if isinstance(array_value, list) and not array_value:
                return None
        except:
            pass

    # Then process the JSONPath
    try:
        jsonpath_expr = parse(path)
        matches = jsonpath_expr.find(parameter)
        
        if not matches:
            raise ProcessParameterMissing("Path not found in parameter")
            
        return matches[0].value
            
    except ProcessParameterMissing:
        raise
    except JsonPathParserError:
        raise ProcessParameterInvalid("Invalid JSONPath expression")
    except Exception as e:
        raise ProcessParameterInvalid("Invalid path syntax or parameter structure")
