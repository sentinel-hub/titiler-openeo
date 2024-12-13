"""titiler.openeo.processes.implementations image methods."""

from typing import Sequence

from .data_model import ImageData

__all__ = ["image_indexes"]


def image_indexes(data: ImageData, indexes: Sequence[int]) -> ImageData:
    """Select indexes from an ImageData."""
    if not all(v > 0 for v in indexes):
        raise IndexError(f"Indexes value must be >= 1, {indexes}")

    if not all(v <= data.count + 1 for v in indexes):
        raise IndexError(f"Indexes value must be =< {data.count + 1}, {indexes}")

    stats = None
    if stats := data.dataset_statistics:
        stats = [stats[ix - 1] for ix in indexes]

    return ImageData(
        data.array[[idx - 1 for idx in indexes]],
        assets=data.assets,
        crs=data.crs,
        bounds=data.bounds,
        band_names=[data.band_names[ix - 1] for ix in indexes],
        metadata=data.metadata,
        dataset_statistics=stats,
        cutline_mask=data.cutline_mask,
    )
