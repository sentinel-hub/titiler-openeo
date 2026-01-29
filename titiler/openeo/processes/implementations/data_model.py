"""TiTiler.openeo data models."""

import logging
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
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

import numpy as np
from rasterio.crs import CRS
from rasterio.features import rasterize
from rasterio.transform import from_bounds
from rasterio.warp import transform_geom
from rio_tiler.constants import MAX_THREADS, WGS84_CRS
from rio_tiler.errors import TileOutsideBounds
from rio_tiler.models import ImageData
from rio_tiler.tasks import TaskType, filter_tasks
from rio_tiler.types import BBox

# https://openeo.org/documentation/1.0/developers/backends/performance.html#datacube-processing
# Here it is important to note that openEO does not enforce or define how the datacube should look like on the backend.
# The datacube can be a set of files, or arrays in memory distributed over a cluster.
# These choices are left to the backend implementor, this guide only tries to highlight the possibilities.
# NOTE: RasterStack is now a class, not a type alias. The class is defined below.
# LazyRasterStack is a deprecated alias for backwards compatibility.

T = TypeVar("T")


def compute_cutline_mask(
    geometry: Dict[str, Any],
    width: int,
    height: int,
    bounds: BBox,
    dst_crs: Optional[CRS] = None,
) -> np.ndarray:
    """
    Compute a cutline mask from geometry without needing an existing ImageData.

    This creates a mask from a geometry (typically from STAC item footprint),
    which indicates which pixels are outside the valid data area.

    Args:
        geometry: GeoJSON geometry dict (typically in EPSG:4326)
        width: Output width in pixels
        height: Output height in pixels
        bounds: Output bounds as (west, south, east, north)
        dst_crs: Target CRS for the geometry transformation

    Returns:
        numpy.ndarray: Boolean mask where True indicates pixels outside the geometry
    """
    # Transform geometry from WGS84 to the destination CRS if needed
    if dst_crs is not None and dst_crs != WGS84_CRS:
        geometry = transform_geom(WGS84_CRS, dst_crs, geometry)

    # Compute affine transform from bounds
    west, south, east, north = bounds
    transform = from_bounds(west, south, east, north, width, height)

    # Create cutline mask using rasterize
    # The mask is True where data is invalid (outside the geometry)
    cutline_mask = rasterize(
        [geometry],
        out_shape=(height, width),
        transform=transform,
        default_value=0,
        fill=1,
        dtype="uint8",
    ).astype("bool")

    return cutline_mask


@dataclass
class ImageRef:
    """A unified image reference that manages lazy or eager data access.

    ImageRef provides a single interface for accessing image metadata and data,
    whether the image is loaded lazily (from a task function) or eagerly (pre-loaded).

    The class tracks its `realized` state:
    - When created with a task function: starts unrealized, loads on first realize()
    - When created with pre-loaded ImageData: starts already realized

    This eliminates the need for isinstance checks - all code works with ImageRef.
    """

    _key: str
    _width: int
    _height: int
    _bounds: BBox
    _crs: Optional[CRS]
    _band_names: List[str]
    _count: int
    _geometry: Optional[Dict[str, Any]] = None
    _task_fn: Optional[Callable[[], ImageData]] = field(default=None, repr=False)
    _image: Optional[ImageData] = field(default=None, repr=False)
    _cutline_mask_cache: Optional[np.ndarray] = field(default=None, repr=False)

    @property
    def key(self) -> str:
        """Unique identifier for this image reference."""
        return self._key

    @property
    def geometry(self) -> Optional[Dict[str, Any]]:
        """GeoJSON geometry dict representing the footprint (typically in EPSG:4326)."""
        return self._geometry

    @property
    def width(self) -> int:
        """Output width in pixels."""
        return self._width

    @property
    def height(self) -> int:
        """Output height in pixels."""
        return self._height

    @property
    def bounds(self) -> BBox:
        """Bounding box as (west, south, east, north)."""
        return self._bounds

    @property
    def crs(self) -> Optional[CRS]:
        """Coordinate reference system."""
        return self._crs

    @property
    def band_names(self) -> List[str]:
        """List of band names."""
        return self._band_names

    @property
    def count(self) -> int:
        """Number of bands."""
        return self._count

    @property
    def realized(self) -> bool:
        """Whether the image data has been loaded."""
        return self._image is not None

    def cutline_mask(self) -> Optional[np.ndarray]:
        """Compute or return the cutline mask.

        For unrealized refs with geometry: computes from geometry (no data load).
        For realized refs: returns the image's cutline_mask attribute.
        For refs without geometry and unrealized: returns None.

        The result is cached for subsequent calls.
        """
        # If already realized, use the image's cutline mask
        if self._image is not None:
            return self._image.cutline_mask

        # If we have geometry, compute from it (lazy path)
        if self._geometry is not None:
            if self._cutline_mask_cache is None:
                self._cutline_mask_cache = compute_cutline_mask(
                    geometry=self._geometry,
                    width=self._width,
                    height=self._height,
                    bounds=self._bounds,
                    dst_crs=self._crs,
                )
            return self._cutline_mask_cache

        return None

    def realize(self) -> ImageData:
        """Get the ImageData, loading it if necessary.

        For lazy refs: executes the task function on first call, caches result.
        For eager refs: returns the pre-loaded image immediately.

        Returns:
            ImageData: The image data.
        """
        if self._image is None:
            if self._task_fn is None:
                raise RuntimeError("ImageRef has no task function and no cached image")
            self._image = self._task_fn()
        return self._image

    @classmethod
    def from_task(
        cls,
        key: str,
        task_fn: Callable[[], ImageData],
        width: int,
        height: int,
        bounds: BBox,
        crs: Optional[CRS] = None,
        band_names: Optional[List[str]] = None,
        geometry: Optional[Dict[str, Any]] = None,
    ) -> "ImageRef":
        """Create a lazy ImageRef from a task function.

        The task will be executed when realize() is called.
        """
        return cls(
            _key=key,
            _width=width,
            _height=height,
            _bounds=bounds,
            _crs=crs,
            _band_names=band_names or [],
            _count=len(band_names) if band_names else 0,
            _geometry=geometry,
            _task_fn=task_fn,
            _image=None,
        )

    @classmethod
    def from_image(cls, key: str, image: ImageData) -> "ImageRef":
        """Create an eager ImageRef from pre-loaded ImageData.

        The image is already loaded, so realize() returns it immediately.
        """
        return cls(
            _key=key,
            _width=image.width,
            _height=image.height,
            _bounds=image.bounds,
            _crs=image.crs,
            _band_names=image.band_names or [],
            _count=image.count,
            _geometry=None,
            _task_fn=None,
            _image=image,
        )


# Backwards compatibility aliases
LazyImageRef = ImageRef
EagerImageRef = ImageRef


class RasterStack(Dict[str, ImageData]):
    """A raster stack with lazy loading and temporal awareness.

    This is THE data structure for collections of raster images in titiler-openeo.
    All images share the same spatial extent and CRS.

    This implementation separates unique key generation from temporal metadata:
    - Keys are guaranteed unique identifiers
    - Temporal metadata enables grouping and filtering by datetime
    - LazyImageRef instances enable cutline mask computation without task execution
    """

    def __init__(
        self,
        tasks: TaskType,
        key_fn: Callable[[Dict[str, Any]], str],
        timestamp_fn: Optional[Callable[[Dict[str, Any]], datetime]] = None,
        allowed_exceptions: Optional[Tuple] = None,
        max_workers: int = MAX_THREADS,
        width: Optional[int] = None,
        height: Optional[int] = None,
        bounds: Optional[BBox] = None,
        dst_crs: Optional[CRS] = None,
        band_names: Optional[List[str]] = None,
    ):
        """Initialize a RasterStack.

        Args:
            tasks: The tasks created by rio_tiler.tasks.create_tasks
            key_fn: Function that generates unique keys from assets
            timestamp_fn: Optional function that extracts datetime objects from assets
            allowed_exceptions: Exceptions allowed during task execution
            max_workers: Maximum number of threads for concurrent execution
            width: Output width in pixels (for LazyImageRef)
            height: Output height in pixels (for LazyImageRef)
            bounds: Output bounds as (west, south, east, north) (for LazyImageRef)
            dst_crs: Target CRS (for LazyImageRef)
            band_names: List of band names (for LazyImageRef)
        """
        super().__init__()
        self._tasks = tasks
        self._allowed_exceptions = allowed_exceptions or (TileOutsideBounds,)
        self._max_workers = max_workers
        self._key_fn = key_fn
        self._timestamp_fn = timestamp_fn

        # Output dimensions for LazyImageRef
        self._width = width
        self._height = height
        self._bounds = bounds
        self._dst_crs = dst_crs
        self._band_names = band_names or []

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

        # LazyImageRef instances for deferred cutline computation
        self._image_refs: Dict[str, LazyImageRef] = {}

        self._compute_metadata()

    @property
    def width(self) -> Optional[int]:
        """Output width in pixels."""
        return self._width

    @property
    def height(self) -> Optional[int]:
        """Output height in pixels."""
        return self._height

    @property
    def bounds(self) -> Optional[BBox]:
        """Output bounds as (west, south, east, north)."""
        return self._bounds

    @property
    def dst_crs(self) -> Optional[CRS]:
        """Target CRS."""
        return self._dst_crs

    @property
    def band_names(self) -> List[str]:
        """List of band names."""
        return self._band_names

    def get_image_ref(self, key: str) -> Optional[LazyImageRef]:
        """Get the LazyImageRef for a given key.

        Args:
            key: The key to look up

        Returns:
            LazyImageRef if available, None otherwise
        """
        return self._image_refs.get(key)

    def get_image_refs(self) -> List[Tuple[str, LazyImageRef]]:
        """Get all LazyImageRef instances in temporal order.

        Returns:
            List of (key, LazyImageRef) tuples in temporal order
        """
        return [
            (key, self._image_refs[key])
            for key in self._keys
            if key in self._image_refs
        ]

    def _compute_metadata(self) -> None:
        """Compute keys, build timestamp mapping, and create ImageRef instances."""
        for i, (task_fn, asset) in enumerate(self._tasks):
            key = self._key_fn(asset)
            self._keys.append(key)
            self._key_to_task_index[key] = i

            if self._timestamp_fn:
                timestamp = self._timestamp_fn(asset)
                self._timestamp_map[key] = timestamp

                if timestamp not in self._timestamp_groups:
                    self._timestamp_groups[timestamp] = []
                self._timestamp_groups[timestamp].append(key)

            # Create ImageRef if we have the required dimensions
            if (
                self._width is not None
                and self._height is not None
                and self._bounds is not None
            ):
                geometry = asset.get("geometry") if isinstance(asset, dict) else None

                # Create a closure that captures the task_fn for this specific item
                def make_task_executor(tf: Any) -> Callable[[], ImageData]:
                    def executor() -> ImageData:
                        if isinstance(tf, Future):
                            return tf.result()
                        else:
                            return tf()

                    return executor

                self._image_refs[key] = ImageRef.from_task(
                    key=key,
                    task_fn=make_task_executor(task_fn),
                    width=self._width,
                    height=self._height,
                    bounds=self._bounds,
                    crs=self._dst_crs,
                    band_names=self._band_names,
                    geometry=geometry,
                )

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
            raise KeyError(f"Key '{key}' not found in RasterStack")

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

    @property
    def first(self) -> ImageData:
        """Get first item (in temporal/key order).

        Returns:
            ImageData: The first item in the stack

        Raises:
            KeyError: If the stack is empty or first task fails
        """
        if not self._keys:
            raise KeyError("RasterStack is empty")

        # Try each key in order until we find one that succeeds
        for key in self._keys:
            try:
                return self[key]
            except KeyError:
                continue
        raise KeyError("No successful tasks found in RasterStack")

    @property
    def last(self) -> ImageData:
        """Get last item (in temporal/key order).

        Returns:
            ImageData: The last item in the stack

        Raises:
            KeyError: If the stack is empty or all tasks fail
        """
        if not self._keys:
            raise KeyError("RasterStack is empty")

        # Try each key in reverse order until we find one that succeeds
        for key in reversed(self._keys):
            try:
                return self[key]
            except KeyError:
                continue
        raise KeyError("No successful tasks found in RasterStack")

    @classmethod
    def from_images(cls, images: Dict[str, ImageData]) -> "RasterStack":
        """Create a RasterStack from pre-loaded ImageData instances.

        This wraps existing ImageData in the RasterStack interface for consistency.
        The images are already loaded, so no lazy evaluation occurs.

        Args:
            images: Dictionary mapping keys to ImageData instances

        Returns:
            RasterStack: A stack containing the provided images

        Raises:
            ValueError: If images is empty
        """
        if not images:
            raise ValueError("Cannot create RasterStack from empty images dict")

        # Get first image for dimension parameters
        first_key = next(iter(images))
        first_img = images[first_key]

        # Create tasks that return pre-loaded images
        # Each task is a tuple of (callable, asset_dict)
        tasks = []
        for key, img in images.items():
            # Create a closure that captures the image
            def make_task(captured_img: ImageData) -> Callable[[], ImageData]:
                return lambda: captured_img

            tasks.append((make_task(img), {"id": key}))

        return cls(
            tasks=tasks,
            key_fn=lambda asset: asset["id"],
            width=first_img.width,
            height=first_img.height,
            bounds=first_img.bounds,
            dst_crs=first_img.crs,
            band_names=first_img.band_names if first_img.band_names else [],
        )


# Deprecated alias for backwards compatibility
# TODO: Remove in a future version
LazyRasterStack = RasterStack
