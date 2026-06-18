"""Reference virtual band plugins shipped with titiler-openeo."""

from typing import List

import numpy
import pystac
from rio_tiler.models import ImageData

from .base import BandMetadata, VirtualBandPlugin, band_array

__all__ = ["NormalizedDifferencePlugin", "ConstantFromPropertyPlugin"]


class NormalizedDifferencePlugin(VirtualBandPlugin):
    """Compute a normalized difference index from two real bands.

    Example configuration entry::

        {"plugin": "normalized_difference",
         "options": {"name": "NDVI", "a": "B08_10m", "b": "B04_10m"}}

    Produces a band ``name`` equal to ``(a - b) / (a + b)``. This is a trivial
    reference implementation that exercises the band-math path of the virtual
    band mechanism.
    """

    def __init__(self, name: str, a: str, b: str, **options):
        """Initialize with the output band name and the two source bands."""
        super().__init__(name=name, a=a, b=b, **options)
        self.name = name
        self.a = a
        self.b = b

    def provided_bands(self) -> List[BandMetadata]:
        """Advertise the single normalized-difference band."""
        return [
            BandMetadata(
                name=self.name,
                description=f"Normalized difference ({self.a} - {self.b}) / "
                f"({self.a} + {self.b})",
                dtype="float32",
            )
        ]

    def required_bands(self) -> List[str]:
        """The two real bands needed for the computation."""
        return [self.a, self.b]

    def compute(
        self,
        name: str,
        items: List[pystac.Item],
        image: ImageData,
    ) -> numpy.ndarray:
        """Return ``(a - b) / (a + b)`` aligned to ``image``."""
        a = band_array(image, self.a).astype("float32")
        b = band_array(image, self.b).astype("float32")
        denom = a + b
        # Avoid division warnings/`inf`; masked where denominator is zero.
        result = numpy.ma.masked_array(
            numpy.where(denom != 0, (a - b), 0),
            mask=numpy.ma.getmaskarray(a) | numpy.ma.getmaskarray(b) | (denom == 0),
        )
        result = result / numpy.ma.masked_equal(denom, 0)
        return result.astype("float32")


class ConstantFromPropertyPlugin(VirtualBandPlugin):
    """Broadcast a per-scene STAC item property value as a constant band.

    This demonstrates wiring values that are *not* raster assets: the value comes
    from ``item.properties`` rather than from pixel data. It is the generic
    pattern behind reconstructing Sentinel-2 angle bands (e.g. a scene's mean
    view/sun angle) as a constant raster.

    Example configuration entry::

        {"plugin": "constant_from_property",
         "options": {"name": "viewZenithMean", "property": "view:incidence_angle"}}

    The band has no ``required_bands``; it still needs a real band to be requested
    alongside it so the output grid (shape/CRS/bounds) is defined.
    """

    def __init__(self, name: str, property: str, default=None, **options):
        """Initialize with the output band name and source item property key."""
        super().__init__(name=name, property=property, default=default, **options)
        self.name = name
        self.property = property
        self.default = default

    def provided_bands(self) -> List[BandMetadata]:
        """Advertise the single property-derived band."""
        return [
            BandMetadata(
                name=self.name,
                description=f"Constant raster from STAC item property "
                f"'{self.property}'",
                dtype="float32",
            )
        ]

    def required_bands(self) -> List[str]:
        """No real bands are needed; the value comes from item metadata."""
        return []

    def compute(
        self,
        name: str,
        items: List[pystac.Item],
        image: ImageData,
    ) -> numpy.ndarray:
        """Broadcast the property value across ``image``'s grid as a constant."""
        value = None
        for item in items:
            candidate = item.properties.get(self.property)
            if candidate is not None:
                value = candidate
                break
        if value is None:
            value = self.default
        if value is None:
            raise ValueError(
                f"Property '{self.property}' not found on items for virtual band "
                f"'{self.name}' and no default provided"
            )

        height, width = image.array.shape[-2:]
        return numpy.ma.masked_array(
            numpy.full((height, width), float(value), dtype="float32"),
            mask=False,
        )
