"""Base classes for titiler-openeo virtual bands plugins.

A *virtual band* is a band that does not exist as a raster asset in the source
STAC catalog but can be computed at read time from STAC item metadata and/or from
the pixel values of real bands. Plugins are bound to collections through
configuration (see :mod:`titiler.openeo.virtualbands.registry`).
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Optional

import numpy
import pystac
from rio_tiler.models import ImageData

__all__ = ["BandMetadata", "VirtualBandPlugin", "band_array"]


@dataclass
class BandMetadata:
    """Metadata describing a virtual band.

    Used to advertise the band in the collection's ``cube:dimensions`` band
    dimension (and, optionally, in ``summaries.eo:bands``) so openEO clients
    accept it as a valid band name.
    """

    name: str
    description: str = ""
    dtype: str = "float32"
    common_name: Optional[str] = None
    unit: Optional[str] = None


class VirtualBandPlugin(ABC):
    """Base class for a collection-bound virtual band plugin.

    Subclasses are instantiated with the ``options`` dictionary from the
    configuration entry, passed as keyword arguments. They declare the band(s)
    they provide, the real asset bands they need to compute them, and a
    ``compute`` method invoked lazily at read time.
    """

    def __init__(self, **options):
        """Store plugin options from the configuration entry."""
        self.options = options

    @abstractmethod
    def provided_bands(self) -> List[BandMetadata]:
        """Return metadata for the band(s) this plugin adds to the collection."""
        ...

    def required_bands(self) -> List[str]:
        """Real asset band names needed to compute the provided band(s).

        Returns an empty list for purely metadata-derived bands. The loader
        reads these bands even when the user did not request them in the output.
        """
        return []

    @abstractmethod
    def compute(
        self,
        name: str,
        items: List[pystac.Item],
        image: ImageData,
    ) -> numpy.ndarray:
        """Compute a single virtual band.

        Args:
            name: The provided band name being requested.
            items: The source STAC items for the slice being materialized
                (e.g. all items mosaicked for one datetime). Use these for
                metadata-derived bands (scalar properties, etc.).
            image: ImageData of the already-read real bands for this slice. Its
                ``band_names`` are the real band names, so :func:`band_array` can
                look bands up by name. Use ``image.bounds``/``image.crs`` and the
                array shape to align the output to the grid.

        Returns:
            A 2D ``(H, W)`` or 3D ``(1, H, W)`` array aligned to ``image``.
        """
        ...


def band_array(image: ImageData, name: str) -> numpy.ndarray:
    """Return the ``(H, W)`` array for band ``name`` from ``image``.

    Relies on ``image.band_names`` being set to the real band names (the loader
    does this before invoking :meth:`VirtualBandPlugin.compute`).
    """
    try:
        idx = list(image.band_names).index(name)
    except ValueError as err:
        raise ValueError(
            f"Required band '{name}' not found in image bands {list(image.band_names)}"
        ) from err
    return image.array[idx]
