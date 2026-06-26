"""titiler.openeo.processes indices."""

from datetime import datetime
from typing import Callable, Dict

from rio_tiler.constants import MAX_THREADS

from .data_model import ImageData, RasterStack
from .math import normalized_difference

__all__ = ["ndvi", "ndwi"]


def _apply_per_slice(
    data: RasterStack, fn: Callable[[ImageData], ImageData]
) -> RasterStack:
    """Apply ``fn`` to every slice and return a new stack.

    When ``data`` has a single downstream consumer (tagged by the
    reference-counted results cache), the source cube is streamed: slices are
    read in concurrent windows and **released as soon as they are consumed**, so
    the whole source cube and the whole result cube are never both fully resident
    (the within-node peak that reference-counted eviction alone can't remove).
    Otherwise the source might be needed by another node, so we fall back to the
    plain non-mutating realization.
    """
    result: Dict[datetime, ImageData] = {}

    if not getattr(data, "_single_consumer", False):
        for key, img_data in data.items():
            result[key] = fn(img_data)
        return RasterStack.from_images(result)

    keys = list(data.keys())
    window = max(1, getattr(data, "_max_workers", MAX_THREADS))
    for start in range(0, len(keys), window):
        batch = keys[start : start + window]
        data.prefetch(batch)  # load the window concurrently
        for key in batch:
            # Avoid re-executing tasks that failed during prefetch (prefetch skips
            # allowed exceptions without caching).
            with data._cache_lock:
                img_data = data._data_cache.get(key)
            if img_data is None:
                continue
            result[key] = fn(img_data)
            data.release(key)  # safe: this stack has no other consumer
    return RasterStack.from_images(result)


def _apply_ndvi(data: ImageData, nir: int, red: int) -> ImageData:
    """Apply NDVI to a single ImageData."""
    nirb = data.array[int(nir) - 1]
    redb = data.array[int(red) - 1]

    return ImageData(
        normalized_difference(nirb, redb),
        assets=data.assets,
        crs=data.crs,
        bounds=data.bounds,
        band_descriptions=[
            "ndvi",
        ],
    )


def _apply_ndwi(data: ImageData, nir: int, swir: int) -> ImageData:
    """Apply NDWI to a single ImageData."""
    nirb = data.array[int(nir) - 1]
    swirb = data.array[int(swir) - 1]

    return ImageData(
        normalized_difference(nirb, swirb),
        assets=data.assets,
        crs=data.crs,
        bounds=data.bounds,
        band_descriptions=[
            "ndwi",
        ],
    )


def ndwi(data: RasterStack, nir: int, swir: int) -> RasterStack:
    """Apply NDWI to RasterStack.

    Args:
        data: RasterStack to process
        nir: Index of the NIR band (1-based)
        swir: Index of the SWIR band (1-based)

    Returns:
        RasterStack with NDWI results
    """
    # Apply NDWI to each item in the stack (streaming when sole consumer)
    return _apply_per_slice(data, lambda img: _apply_ndwi(img, nir, swir))


def ndvi(data: RasterStack, nir: int, red: int) -> RasterStack:
    """Apply NDVI to RasterStack.

    Args:
        data: RasterStack to process
        nir: Index of the NIR band (1-based)
        red: Index of the red band (1-based)

    Returns:
        RasterStack (Dict[datetime, ImageData]) containing NDVI results
    """
    # Apply NDVI to each item in the stack (streaming when sole consumer)
    return _apply_per_slice(data, lambda img: _apply_ndvi(img, nir, red))
