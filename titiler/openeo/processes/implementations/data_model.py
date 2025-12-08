"""TiTiler.openeo data models."""

import warnings
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple, TypeVar, Union, cast

from rio_tiler.errors import TileOutsideBounds
from rio_tiler.models import ImageData
from rio_tiler.tasks import TaskType, filter_tasks

# https://openeo.org/documentation/1.0/developers/backends/performance.html#datacube-processing
# Here it is important to note that openEO does not enforce or define how the datacube should look like on the backend.
# The datacube can be a set of files, or arrays in memory distributed over a cluster.
# These choices are left to the backend implementor, this guide only tries to highlight the possibilities.
RasterStack = Dict[str, ImageData]

T = TypeVar("T")


def to_raster_stack(data: Union[ImageData, RasterStack]) -> RasterStack:
    """Convert ImageData to RasterStack if necessary.

    Args:
        data: ImageData or RasterStack to convert

    Returns:
        RasterStack: Always a RasterStack, even if input was a single ImageData
    """
    if isinstance(data, ImageData):
        # Convert single ImageData to a RasterStack with one item
        # Using "data" as the key for single images
        return {"data": data}
    return data


class LazyRasterStack(Dict[str, ImageData]):
    """A RasterStack that lazily loads data when accessed.

    This implementation separates unique key generation from temporal metadata:
    - Keys are guaranteed unique identifiers
    - Temporal metadata enables grouping and filtering by datetime
    """

    def __init__(
        self,
        tasks: TaskType,
        key_fn: Optional[Callable[[Dict[str, Any]], str]] = None,
        timestamp_fn: Optional[Callable[[Dict[str, Any]], datetime]] = None,
        allowed_exceptions: Optional[Tuple] = None,
        # Deprecated parameters for backward compatibility
        date_name_fn: Optional[Callable[[Dict[str, Any]], str]] = None,
    ):
        """Initialize a LazyRasterStack.

        Args:
            tasks: The tasks created by rio_tiler.tasks.create_tasks
            key_fn: Function that generates unique keys from assets
            timestamp_fn: Optional function that extracts datetime objects from assets
            allowed_exceptions: Exceptions allowed during task execution
            date_name_fn: DEPRECATED. Use key_fn for unique keys and timestamp_fn for temporal metadata.
                         This parameter will be removed in a future version.
        """
        super().__init__()
        self._tasks = tasks
        self._allowed_exceptions = allowed_exceptions or (TileOutsideBounds,)
        self._executed = False
        
        # Handle backward compatibility and parameter validation
        if date_name_fn is not None:
            warnings.warn(
                "The 'date_name_fn' parameter is deprecated. Use 'key_fn' for unique keys "
                "and 'timestamp_fn' for temporal metadata instead. "
                "This parameter will be removed in a future version.",
                DeprecationWarning,
                stacklevel=2
            )
            # Legacy usage - convert to new API
            if key_fn is None:
                key_fn = date_name_fn
            if timestamp_fn is None:
                timestamp_fn = date_name_fn
        elif key_fn is None:
            raise TypeError("LazyRasterStack() missing required argument: 'key_fn' (or use deprecated 'date_name_fn')")
        
        self._key_fn = key_fn
        self._timestamp_fn = timestamp_fn
        
        # Pre-compute keys and timestamp metadata
        self._keys = []
        self._timestamp_map = {}  # Maps keys to datetime objects  
        self._timestamp_groups = {}  # Maps datetime objects to lists of keys
        
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
        
        # If we have timestamps, sort keys by temporal order
        if self._timestamp_fn and self._timestamp_map:
            # Create a list of (timestamp, key) pairs and sort by timestamp
            timestamp_key_pairs = [(self._timestamp_map[key], key) for key in self._keys]
            timestamp_key_pairs.sort(key=lambda x: x[0])  # Sort by timestamp
            # Update _keys to be in temporal order
            self._keys = [key for _, key in timestamp_key_pairs]

    def _execute_tasks(self) -> None:
        """Execute the tasks and populate the dictionary."""
        if not self._executed:
            for data, asset in filter_tasks(
                self._tasks, allowed_exceptions=self._allowed_exceptions
            ):
                key = self._key_fn(asset)
                self[key] = data
            self._executed = True

    def timestamps(self) -> List[datetime]:
        """Return list of unique timestamps in the stack."""
        return sorted(self._timestamp_groups.keys())
    
    def get_timestamp(self, key: str) -> Optional[datetime]:
        """Get the timestamp associated with a key."""
        return self._timestamp_map.get(key)
    
    def get_by_timestamp(self, timestamp: datetime) -> Dict[str, ImageData]:
        """Get all items with the specified timestamp.
        
        Args:
            timestamp: datetime object
            
        Returns:
            Dictionary mapping keys to ImageData for items with this timestamp
        """
        if timestamp not in self._timestamp_groups:
            return {}
        
        if not self._executed:
            self._execute_tasks()
        
        return {key: self[key] for key in self._timestamp_groups[timestamp] if key in self}
    
    def groupby_timestamp(self) -> Dict[datetime, Dict[str, ImageData]]:
        """Group items by timestamp.
        
        Returns:
            Dictionary mapping datetime objects to dictionaries of {key: ImageData}
        """
        if not self._executed:
            self._execute_tasks()
        
        result = {}
        for timestamp in self._timestamp_groups:
            result[timestamp] = self.get_by_timestamp(timestamp)
        return result

    def __getitem__(self, key: str) -> ImageData:
        """Get an item from the RasterStack, executing tasks if necessary."""
        if not self._executed:
            self._execute_tasks()
        return super().__getitem__(key)

    def __iter__(self) -> Any:
        """Iterate over the keys of the RasterStack."""
        return iter(self._keys)

    def __len__(self) -> int:
        """Return the number of items in the RasterStack."""
        return len(self._keys)

    def __contains__(self, key: object) -> bool:
        """Check if a key is in the RasterStack."""
        return key in self._keys

    def keys(self) -> Any:
        """Return the keys of the RasterStack."""
        return self._keys

    def values(self) -> Any:
        """Return the values of the RasterStack, executing tasks if necessary."""
        if not self._executed:
            self._execute_tasks()
        # Return values in temporal order
        return [self[key] for key in self._keys]

    def items(self) -> Any:
        """Return the items of the RasterStack, executing tasks if necessary."""
        if not self._executed:
            self._execute_tasks()
        # Return items in temporal order
        return [(key, self[key]) for key in self._keys]

    def get(self, key: str, default: Optional[T] = None) -> Union[ImageData, T]:
        """Get an item from the RasterStack, executing tasks if necessary."""
        if key not in self:
            return cast(T, default)
        return self[key]
