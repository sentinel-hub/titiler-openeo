"""TiTiler.openeo data models."""

from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple, TypeVar, Union, cast

from rio_tiler.errors import TileOutsideBounds
from rio_tiler.models import ImageData
from rio_tiler.tasks import TaskType, filter_tasks

# https://openeo.org/documentation/1.0/developers/backends/performance.html#datacube-processing
# Here it is important to note that openEO does not enforce or define how the datacube should look like on the backend.
# The datacube can be a set of files, or arrays in memory distributed over a cluster.
# These choices are left to the backend implementor, this guide only tries to highlight the possibilities.
RasterStack = Dict[str, ImageData]

T = TypeVar('T')


class LazyRasterStack(Dict[str, ImageData]):
    """A RasterStack that lazily loads data when accessed.
    
    This class wraps the tasks created by rio_tiler.tasks.create_tasks and only executes
    them when the data is actually accessed. This allows for more efficient processing
    when the data is not needed immediately.
    """

    def __init__(
        self,
        tasks: TaskType,
        date_name_fn: Callable[[Dict[str, Any]], str],
        allowed_exceptions: Optional[Tuple] = None,
    ):
        """Initialize a LazyRasterStack.
        
        Args:
            tasks: The tasks created by rio_tiler.tasks.create_tasks
            date_name_fn: A function that extracts a date name from an asset
            allowed_exceptions: Exceptions that are allowed to be raised during task execution
        """
        super().__init__()
        self._tasks = tasks
        self._date_name_fn = date_name_fn
        self._allowed_exceptions = allowed_exceptions or (TileOutsideBounds,)
        self._executed = False
        self._keys = self._compute_keys()

    def _compute_keys(self) -> List[str]:
        """Compute the keys of the RasterStack without executing the tasks."""
        return [self._date_name_fn(asset) for _, asset in self._tasks]

    def _execute_tasks(self) -> None:
        """Execute the tasks and populate the dictionary."""
        if not self._executed:
            for data, asset in filter_tasks(self._tasks, allowed_exceptions=self._allowed_exceptions):
                key = self._date_name_fn(asset)
                self[key] = data
            self._executed = True

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
        return super().values()

    def items(self) -> Any:
        """Return the items of the RasterStack, executing tasks if necessary."""
        if not self._executed:
            self._execute_tasks()
        return super().items()

    def get(self, key: str, default: Optional[T] = None) -> Union[ImageData, T]:
        """Get an item from the RasterStack, executing tasks if necessary."""
        if key not in self:
            return cast(T, default)
        return self[key]
