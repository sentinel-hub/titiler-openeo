"""TiTiler.openeo data models."""

import logging
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime
from typing import (
    Any,
    Callable,
    Dict,
    List,
    Optional,
    Set,
    Tuple,
    TypeVar,
    Union,
    overload,
)

from rio_tiler.constants import MAX_THREADS
from rio_tiler.errors import TileOutsideBounds
from rio_tiler.models import ImageData
from rio_tiler.tasks import TaskType, filter_tasks

# https://openeo.org/documentation/1.0/developers/backends/performance.html#datacube-processing
# Here it is important to note that openEO does not enforce or define how the datacube should look like on the backend.
# The datacube can be a set of files, or arrays in memory distributed over a cluster.
# These choices are left to the backend implementor, this guide only tries to highlight the possibilities.
RasterStack = Dict[str, ImageData]

T = TypeVar("T")


def get_first_item(data: Union[ImageData, RasterStack]) -> ImageData:
    """Get the first item from a RasterStack efficiently.

    For LazyRasterStack, this finds the first successful task.
    For regular RasterStack, this gets the first value.
    For single ImageData, returns it directly.

    Args:
        data: Input data (ImageData or RasterStack)

    Returns:
        ImageData: The first successful item

    Raises:
        KeyError: If no successful tasks are found in the stack
    """
    if isinstance(data, ImageData):
        return data
    elif isinstance(data, LazyRasterStack):
        # Try each key in order until we find one that succeeds
        for key in data.keys():
            try:
                return data[key]  # Execute this task
            except KeyError:
                # This task failed, try the next one
                continue

        # If we get here, all tasks failed
        raise KeyError("No successful tasks found in LazyRasterStack")
    elif isinstance(data, dict):
        # Regular RasterStack
        return next(iter(data.values()))
    else:
        raise ValueError(f"Unsupported data type: {type(data)}")


def get_last_item(data: Union[ImageData, RasterStack]) -> ImageData:
    """Get the last item from a RasterStack efficiently.

    For LazyRasterStack, this finds the last successful task.
    For regular RasterStack, this gets the last value.
    For single ImageData, returns it directly.

    Args:
        data: Input data (ImageData or RasterStack)

    Returns:
        ImageData: The last successful item

    Raises:
        KeyError: If no successful tasks are found in the stack
    """
    if isinstance(data, ImageData):
        return data
    elif isinstance(data, LazyRasterStack):
        # Try each key in reverse order until we find one that succeeds
        for key in reversed(list(data.keys())):
            try:
                return data[key]  # Execute this task
            except KeyError:
                # This task failed, try the previous one
                continue

        # If we get here, all tasks failed
        raise KeyError("No successful tasks found in LazyRasterStack")
    elif isinstance(data, dict):
        # Regular RasterStack - get last value
        return list(data.values())[-1]
    else:
        raise ValueError(f"Unsupported data type: {type(data)}")


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
        key_fn: Callable[[Dict[str, Any]], str],
        timestamp_fn: Optional[Callable[[Dict[str, Any]], datetime]] = None,
        allowed_exceptions: Optional[Tuple] = None,
        max_workers: int = MAX_THREADS,
    ):
        """Initialize a LazyRasterStack.

        Args:
            tasks: The tasks created by rio_tiler.tasks.create_tasks
            key_fn: Function that generates unique keys from assets
            timestamp_fn: Optional function that extracts datetime objects from assets
            allowed_exceptions: Exceptions allowed during task execution
            max_workers: Maximum number of threads for concurrent execution
        """
        super().__init__()
        self._tasks = tasks
        self._allowed_exceptions = allowed_exceptions or (TileOutsideBounds,)
        self._max_workers = max_workers
        self._key_fn = key_fn
        self._timestamp_fn = timestamp_fn

        # Per-key execution cache instead of global execution flag
        self._data_cache: Dict[str, ImageData] = {}
        self._cache_lock = threading.Lock()  # Thread-safe cache access

        # Pre-compute keys and timestamp metadata
        self._keys: List[str] = []
        self._key_to_task_index: Dict[str, int] = {}  # Maps keys to task indices
        self._timestamp_map: Dict[str, datetime] = {}  # Maps keys to datetime objects
        self._timestamp_groups: Dict[
            datetime, List[str]
        ] = {}  # Maps datetime objects to lists of keys

        self._compute_metadata()

    def _compute_metadata(self) -> None:
        """Compute keys and build timestamp mapping without executing tasks."""
        for i, (_, asset) in enumerate(self._tasks):
            key = self._key_fn(asset)
            self._keys.append(key)
            self._key_to_task_index[key] = i

            if self._timestamp_fn:
                timestamp = self._timestamp_fn(asset)
                self._timestamp_map[key] = timestamp

                if timestamp not in self._timestamp_groups:
                    self._timestamp_groups[timestamp] = []
                self._timestamp_groups[timestamp].append(key)

        # If we have timestamps, sort keys by temporal order
        if self._timestamp_fn and self._timestamp_map:
            # Create a list of (timestamp, key) pairs and sort by timestamp
            timestamp_key_pairs = [
                (self._timestamp_map[key], key) for key in self._keys
            ]
            timestamp_key_pairs.sort(key=lambda x: x[0])  # Sort by timestamp
            # Update _keys to be in temporal order
            self._keys = [key for _, key in timestamp_key_pairs]

            # IMPORTANT: The _key_to_task_index mapping is still correct
            # because it maps keys to their original task indices,
            # regardless of the order in _keys

    def _execute_task(self, key: str, task_func: Any) -> ImageData:
        """Execute a single task and return the result.

        Args:
            key: The key for error reporting
            task_func: The task function or Future to execute

        Returns:
            ImageData: The result of the task execution
        """
        if isinstance(task_func, Future):
            return task_func.result()
        else:
            return task_func()

    def _execute_selected_tasks(self, selected_keys: Set[str]) -> None:
        """Execute tasks for the selected keys only with concurrent execution.

        Args:
            selected_keys: Set of keys to execute tasks for
        """

        # Filter out keys that are already cached
        with self._cache_lock:
            keys_to_execute = [
                key
                for key in selected_keys
                if key in self._key_to_task_index and key not in self._data_cache
            ]

        if not keys_to_execute:
            return

        # Get the tasks for the selected keys with their corresponding keys
        key_task_pairs = [
            (key, self._tasks[self._key_to_task_index[key]]) for key in keys_to_execute
        ]

        # Execute tasks concurrently
        with ThreadPoolExecutor(max_workers=self._max_workers) as executor:
            # Submit all tasks
            key_to_future = {
                key: executor.submit(self._execute_task, key, task_func)
                for key, (task_func, _asset) in key_task_pairs
            }

            # Collect results as they complete
            for key, future in key_to_future.items():
                try:
                    data = future.result()
                    with self._cache_lock:
                        self._data_cache[key] = data
                except self._allowed_exceptions as e:
                    # Log task failures for visibility in production scenarios
                    logging.warning(
                        "Task execution failed for key '%s' during concurrent execution: %s. "
                        "This item will be skipped, which may result in incomplete data.",
                        key,
                        str(e),
                    )
                    # Skip failed tasks, don't cache them
                    continue

    def _execute_all_tasks(self) -> None:
        """Execute all tasks and populate the cache (for backward compatibility)."""
        for data, asset in filter_tasks(
            self._tasks, allowed_exceptions=self._allowed_exceptions
        ):
            key = self._key_fn(asset)
            with self._cache_lock:
                self._data_cache[key] = data

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

        # Execute only tasks for this timestamp
        timestamp_keys = set(self._timestamp_groups[timestamp])
        self._execute_selected_tasks(timestamp_keys)

        return {
            key: self._data_cache[key]
            for key in self._timestamp_groups[timestamp]
            if key in self._data_cache
        }

    def groupby_timestamp(self) -> Dict[datetime, Dict[str, ImageData]]:
        """Group items by timestamp.

        Returns:
            Dictionary mapping datetime objects to dictionaries of {key: ImageData}
        """
        result = {}
        for timestamp in self._timestamp_groups:
            result[timestamp] = self.get_by_timestamp(timestamp)
        return result

    def __getitem__(self, key: str) -> ImageData:
        """Get an item from the RasterStack, executing the task if necessary."""
        with self._cache_lock:
            if key in self._data_cache:
                return self._data_cache[key]

        if key not in self._key_to_task_index:
            raise KeyError(f"Key '{key}' not found in LazyRasterStack")

        task_index = self._key_to_task_index[key]
        task_func, asset = self._tasks[task_index]

        try:
            data = self._execute_task(key, task_func)
            with self._cache_lock:
                self._data_cache[key] = data
            return data
        except self._allowed_exceptions as err:
            raise KeyError(f"Task execution failed for key '{key}'") from err

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
        # Execute all tasks if not already cached
        all_keys = set(self._keys)
        self._execute_selected_tasks(all_keys)
        # Return values in temporal order
        with self._cache_lock:
            return [
                self._data_cache[key] for key in self._keys if key in self._data_cache
            ]

    def items(self) -> Any:
        """Return the items of the RasterStack, executing tasks if necessary."""
        # Execute all tasks if not already cached
        all_keys = set(self._keys)
        self._execute_selected_tasks(all_keys)
        # Return items in temporal order
        with self._cache_lock:
            return [
                (key, self._data_cache[key])
                for key in self._keys
                if key in self._data_cache
            ]

    @overload
    def get(self, key: str) -> Optional[ImageData]: ...

    @overload
    def get(self, key: str, default: T) -> Union[ImageData, T]: ...

    def get(self, key: str, default: Optional[T] = None) -> Union[ImageData, T, None]:
        """Get an item from the RasterStack, executing the task if necessary."""
        if key not in self:
            return default
        try:
            return self[key]  # Uses lazy execution via __getitem__
        except KeyError:
            return default
