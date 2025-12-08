# Issue: LazyRasterStack Uniqueness Problem and Refactoring Proposal

## Problem Statement

### Current Issue
`LazyRasterStack` suffers from a key uniqueness problem that causes data loss when multiple STAC items share the same datetime. Time dimennsions is key to raster stacks, and the current implementation conflates unique identification with temporal metadata.

**Root Cause:**
- `LazyRasterStack` uses `date_name_fn` to generate dictionary keys from asset properties
- The function typically extracts only the datetime: `lambda asset: _props_to_datename(asset["properties"])`
- When multiple items have identical datetimes, they generate the same key
- Dictionary overwrites occur, keeping only the last item with that datetime

**Impact:**
- Data loss: Only the last item with a given datetime survives
- Affects load_stac when multiple items share timestamps
- Downstream projects have implemented workarounds (e.g., appending item IDs to datetimes)

### Example of Current Behavior
```python
# Two items with same datetime
items = [
    {"id": "item-123", "properties": {"datetime": "2023-01-01T00:00:00Z"}},
    {"id": "item-456", "properties": {"datetime": "2023-01-01T00:00:00Z"}},
]

# Current implementation
stack = LazyRasterStack(
    tasks=tasks,
    date_name_fn=lambda asset: asset["properties"]["datetime"],
)

# Result: Only one item remains (dictionary overwrite)
# {"2023-01-01T00:00:00Z": ImageData(...)}  # Lost item-123!
```

## Proposed Solution: Separate Key Generation from Timestamp Metadata

### Design Principles

1. **Separation of Concerns**: Distinguish between unique identification and semantic grouping
2. **No Data Loss**: Guarantee all items are preserved
3. **Backward Compatibility**: Minimize breaking changes where possible
4. **Flexibility**: Enable powerful grouping and filtering operations

### Architecture Overview

#### Core Changes to LazyRasterStack

```python
class LazyRasterStack(Dict[str, ImageData]):
    """A RasterStack that lazily loads data when accessed.
    
    This implementation separates unique key generation from temporal metadata:
    - Keys are guaranteed unique identifiers
    - Temporal metadata enables grouping and filtering by datetime
    """

    def __init__(
        self,
        tasks: TaskType,
        key_fn: Callable[[Dict[str, Any]], str],
        timestamp_fn: Optional[Callable[[Dict[str, Any]], str]] = None,
        allowed_exceptions: Optional[Tuple] = None,
    ):
        """Initialize a LazyRasterStack.

        Args:
            tasks: The tasks created by rio_tiler.tasks.create_tasks
            key_fn: Function that generates unique keys from assets
            timestamp_fn: Optional function that extracts datetime from assets
            allowed_exceptions: Exceptions allowed during task execution
        """
        super().__init__()
        self._tasks = tasks
        self._key_fn = key_fn
        self._timestamp_fn = timestamp_fn
        self._allowed_exceptions = allowed_exceptions or (TileOutsideBounds,)
        self._executed = False
        
        # Pre-compute keys and timestamp metadata
        self._keys = []
        self._timestamp_map = {}  # Maps keys to timestamps
        self._timestamp_groups = {}  # Maps timestamps to lists of keys
        
        self._compute_metadata()

    def _compute_metadata(self) -> None:
        """Compute keys and build timestamp mapping without executing tasks."""
        for _, asset in self._tasks:
            key = self._key_fn(asset)
            self._keys.append(key)
            
            if self._timestamp_fn:
                timestamp = self._timestamp_fn(asset)
                self._timestamp_map[key] = timestamp
                
                if timestamp not in self._timestamp_groups:
                    self._timestamp_groups[timestamp] = []
                self._timestamp_groups[timestamp].append(key)

    # New grouping methods
    def timestamps(self) -> List[str]:
        """Return list of unique timestamps in the stack."""
        return sorted(self._timestamp_groups.keys())
    
    def get_timestamp(self, key: str) -> Optional[str]:
        """Get the timestamp associated with a key."""
        return self._timestamp_map.get(key)
    
    def get_by_timestamp(self, timestamp: str) -> Dict[str, ImageData]:
        """Get all items with the specified timestamp.
        
        Args:
            timestamp: ISO format timestamp string
            
        Returns:
            Dictionary mapping keys to ImageData for items with this timestamp
        """
        if timestamp not in self._timestamp_groups:
            return {}
        
        if not self._executed:
            self._execute_tasks()
        
        return {key: self[key] for key in self._timestamp_groups[timestamp] if key in self}
    
    def groupby_timestamp(self) -> Dict[str, Dict[str, ImageData]]:
        """Group items by timestamp.
        
        Returns:
            Dictionary mapping timestamps to dictionaries of {key: ImageData}
        """
        if not self._executed:
            self._execute_tasks()
        
        result = {}
        for timestamp in self._timestamp_groups:
            result[timestamp] = self.get_by_timestamp(timestamp)
        return result
```

### Usage Examples

#### Example 1: Basic Usage with Unique Keys
```python
# In stacapi.py
tasks = create_tasks(_reader, items, MAX_THREADS, bbox, ...)

stack = LazyRasterStack(
    tasks=tasks,
    key_fn=lambda asset: asset["id"],  # Use item ID as unique key
    timestamp_fn=lambda asset: _props_to_timestamp(asset["properties"]),
    allowed_exceptions=(TileOutsideBounds,),
)

# Access by key
image = stack["item-123"]

# Get all items for a timestamp
items_jan_1 = stack.get_by_timestamp("2023-01-01T00:00:00Z")

# Group by timestamp
grouped = stack.groupby_timestamp()
# {"2023-01-01T00:00:00Z": {"item-123": img1, "item-456": img2}, ...}
```

#### Example 2: Backward Compatibility (Single Item Per Timestamp)
```python
# For cases where timestamps are unique, can use simplified key_fn
stack = LazyRasterStack(
    tasks=tasks,
    key_fn=lambda asset: _props_to_timestamp(asset["properties"]),  # Use timestamp as key
    timestamp_fn=lambda asset: _props_to_timestamp(asset["properties"]),  # Same
)

# Behaves like before if timestamps are unique
image = stack["2023-01-01T00:00:00Z"]
```

#### Example 3: Custom Key Generation
```python
# Use composite keys when needed
def make_composite_key(asset: Dict) -> str:
    """Generate a composite key from collection and item ID."""
    collection = asset.get("collection", "unknown")
    item_id = asset["id"]
    return f"{collection}:{item_id}"

stack = LazyRasterStack(
    tasks=tasks,
    key_fn=make_composite_key,
    timestamp_fn=lambda asset: _props_to_timestamp(asset["properties"]),
)
```

## Implementation Plan

### Phase 1: Core LazyRasterStack Changes
- [ ] Update `LazyRasterStack.__init__` to accept `key_fn` parameter
- [ ] Add `_timestamp_map` and `_timestamp_groups` attributes
- [ ] Implement `_compute_metadata()` method
- [ ] Update `_execute_tasks()` to use `key_fn` instead of `date_name_fn`
- [ ] Add new methods: `timestamps()`, `get_timestamp()`, `get_by_timestamp()`, `groupby_timestamp()`

### Phase 2: Update Call Sites
- [ ] Update `stacapi.py` `_process_spatial_extent()` to use new API
- [ ] Update `io.py` `load_url()` to use new API
- [ ] Review all LazyRasterStack instantiations in codebase

### Phase 3: Testing
- [ ] Add tests for multiple items with same timestamp
- [ ] Add tests for grouping methods
- [ ] Add tests for timestamp metadata access
- [ ] Ensure existing tests still pass
- [ ] Add edge case tests (no timestamp_fn, empty stack, etc.)

### Phase 4: Documentation
- [ ] Update docstrings with examples
- [ ] Add migration guide for downstream users
- [ ] Document new grouping capabilities
- [ ] Update README with new patterns

## Breaking Changes

### API Changes
1. **`timestamp_fn` parameter behavior**: 
   - Before: Used for dictionary keys
   - After: Used only for metadata/grouping
   
2. **Required `key_fn` parameter**:
   - New required parameter must be provided
   - Migration: Set `key_fn=timestamp_fn` for backward compatible behavior

### Migration Strategy

#### For Upstream (this repo)
```python
# Before
LazyRasterStack(
    tasks=tasks,
    timestamp_fn=lambda asset: _props_to_timestamp(asset["properties"]),
)

# After
LazyRasterStack(
    tasks=tasks,
    key_fn=lambda asset: asset["id"],  # Use unique ID
    timestamp_fn=lambda asset: _props_to_timestamp(asset["properties"]),
)
```

## Benefits

1. **Data Integrity**: No more lost items due to key collisions
2. **Clarity**: Explicit separation between identity and temporal metadata
3. **Flexibility**: Rich grouping and filtering capabilities
4. **Correctness**: Properly models the reality that multiple items can share timestamps
5. **Extensibility**: Easy to add more metadata relationships in the future

## Alternatives Considered

### Option 1: Auto-append Counter
- Auto-detect duplicates and append counter
- Rejected: Hidden behavior, unclear key naming

### Option 2: List-based Structure
- Change to `Dict[str, List[ImageData]]`
- Rejected: Breaking change, complicates simple cases

### Option 3: Composite Keys
- Create keys like `"2023-01-01T00:00:00Z_item-123"`
- Rejected: Less clean separation, harder to extract timestamp from key

## References

- Downstream fix by Vincent: `/home/emathot/Workspace/eopf-explorer/titiler-eopf/titiler/eopf/openeo/processes/implementations/io.py`
- Current implementation: `titiler/openeo/processes/implementations/data_model.py`
- Usage in stacapi: `titiler/openeo/stacapi.py` (line ~900)

## Open Questions

1. Should `key_fn` be optional with a sensible default (e.g., `lambda asset: asset["id"]`)?
   --> Yes, default to item ID for uniqueness.
2. Should we provide helper functions for common key generation patterns?
    --> Yes, provide helpers for common patterns.
3. Should `timestamp_fn` remain optional or become required for full functionality?
   --> It becomes mandatory because time dimension is core to raster stacks.
4. Do we need a deprecation period for the old API?
   --> No
