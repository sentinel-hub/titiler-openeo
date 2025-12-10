# Type Validation in OpenEO Process Implementations

## Overview

The OpenEO process implementations in `titiler/openeo/processes/implementations/` now include automatic runtime type validation using Pydantic. This helps catch type errors early, such as passing a `RasterStack` to a function that expects an array-like object.

## How It Works

### Automatic Validation

The `@process` decorator automatically validates parameter types based on function signatures:

```python
@process
def array_create(data: Optional[ArrayLike] = None, repeat: int = 1) -> ArrayLike:
    """Creates a new array."""
    # Type validation happens automatically before this code runs
    if data is None:
        return numpy.empty((1, 1, 1), dtype=numpy.uint8)
    return numpy.asanyarray(data)
```

### Type Checking Features

1. **Type Annotations as Source of Truth**: Function type hints define expected types
2. **Pydantic Validation**: Leverages Pydantic's `TypeAdapter` for runtime validation
3. **Special Handling for Common Mistakes**:
   - Detects when `RasterStack` (dict) is passed to array-like parameters
   - Provides clear, actionable error messages

### Example Error Messages

When a user passes a `RasterStack` to `array_create`:

```python
TypeError: Parameter 'data' in process 'array_create' expected 
'Union[ArrayLike, None]' but got 'dict'. RasterStack/dict types 
are not compatible with array-like parameters.
```

## Validation Rules

### What Gets Validated

- All parameters with type annotations
- Parameters resolved through OpenEO's parameter reference system
- Optional types (allows `None` when appropriate)

### What Doesn't Get Validated

- Parameters without type annotations
- Parameters with `Any` type
- Special internal parameters (e.g., `_openeo_user`)

### Type Compatibility

The validation system understands:

- **Optional types**: `Optional[ArrayLike]` accepts `None` or array-like values
- **Union types**: `Union[int, float]` accepts either integers or floats
- **Generic types**: `Dict[str, ImageData]` for RasterStack
- **Custom types**: `RasterStack`, `LazyRasterStack`, `ArrayLike`

## Common Type Errors

### RasterStack vs ArrayLike

**Problem**: Passing a `RasterStack` to a function expecting arrays

```python
# ❌ This will raise a TypeError
raster_stack = {"band1": ImageData(...)}
result = array_create(data=raster_stack)
```

**Solution**: Use the correct input type

```python
# ✅ Pass an array instead
result = array_create(data=[1, 2, 3])
```

### None for Required Parameters

**Problem**: Passing `None` to non-optional parameters

```python
# ❌ This will raise a TypeError if 'data' is not Optional
result = some_process(data=None)
```

**Solution**: Check if the parameter is Optional in the function signature

## Implementation Details

### The @process Decorator

The `@process` decorator in `titiler/openeo/processes/implementations/core.py`:

1. Resolves parameter references
2. Extracts type annotations from function signature
3. Validates each parameter using `_validate_parameter_types()`
4. Executes the function if all validations pass

### Validation Function

The `_validate_parameter_types()` function:

- Checks for None values in non-Optional parameters
- Detects dict/RasterStack being passed to non-dict parameters
- Uses Pydantic's TypeAdapter for general validation
- Gracefully skips validation for complex types Pydantic can't handle

### Performance Considerations

Type validation adds minimal overhead:

- Validation only runs for decorated functions
- Failed validations raise errors early, preventing downstream issues
- Pydantic's TypeAdapter is efficient for most common types

## Testing Type Validation

Example test to verify type validation:

```python
from titiler.openeo.processes.implementations.arrays import array_create
from titiler.openeo.processes.implementations.data_model import RasterStack

def test_array_create_rejects_raster_stack():
    """Verify that array_create rejects RasterStack input."""
    raster_stack: RasterStack = {
        "band1": ImageData(
            np.array([[[1, 2], [3, 4]]]),
            bounds=(0, 0, 1, 1),
            crs="EPSG:4326"
        )
    }
    
    with pytest.raises(TypeError) as exc_info:
        array_create(data=raster_stack)
    
    assert "RasterStack/dict types are not compatible" in str(exc_info.value)
```

## Adding Type Validation to New Processes

To add type validation to a new process:

1. **Add the @process decorator**:

   ```python
   from .core import process
   
   @process
   def my_process(data: ArrayLike, scale: float) -> ArrayLike:
       ...
   ```

2. **Use proper type hints**:
   - Use `ArrayLike` for array inputs
   - Use `RasterStack` for dict inputs
   - Use `Optional[T]` for optional parameters
   - Use `Union[T1, T2]` for multiple accepted types

3. **Test edge cases**:
   - Test with None values
   - Test with wrong types
   - Test with valid inputs

## Troubleshooting

### ValidationError from Pydantic

If you see a Pydantic `ValidationError`, check:

1. The expected type in the function signature
2. The actual type being passed
3. Whether the types are compatible

### "Could not validate type" Debug Messages

These are logged when Pydantic can't validate a complex type. This is usually fine and the validation is skipped for those parameters.

### Subscripted Generics Error

If you see "Subscripted generics cannot be used with class and instance checks":

- This happens when using type aliases like `RasterStack` in `isinstance()` checks
- The validation code handles this correctly by checking for `dict` instead

## Future Enhancements

Possible improvements:

- Add validation for return types
- Create more specific error messages for common mistakes
- Add configuration to enable/disable validation per process
- Support for custom validation rules per parameter
