"""Generic rectilinear-grid bilinear interpolation.

Shared by band-source readers that evaluate a coarse grid at arbitrary
destination coordinates: Sentinel-1's calibration/noise LUTs
(`titiler.openeo.sar.annotation`, docs/adr/0002-band-sources.md) and
Sentinel-2's sun-angle grid (`titiler.openeo.sentinel2.tile_metadata`,
docs/adr/0004-sentinel2-view-sun-angle-bands.md). Nothing here is specific
to either -- both use it, and neither `sar`/`sentinel2` is the natural
owner over the other, hence its own top-level module. Deliberately not
under `bandsources/`: that package's own `__init__.py` imports both
`sar`- and `sentinel2`-rooted readers, so a module living inside it would
make `from ..bandsources.grid2d import Grid2D` force the whole
`bandsources` package to initialize first -- circular with `sentinel2`'s
own import of `bandsources` (confirmed empirically: importing
`sentinel2.tile_metadata` before anything else raised `ImportError:
cannot import name 'TileMetadata' from partially initialized module`).
"""

from dataclasses import dataclass
from typing import Dict

import numpy as np

__all__ = ["Grid2D"]


@dataclass(frozen=True)
class Grid2D:
    """A value sampled on a rectilinear (line, pixel) grid, bilinearly interpolable."""

    lines: np.ndarray
    pixels: np.ndarray
    values: Dict[str, np.ndarray]

    def interp(self, name: str, line: np.ndarray, pixel: np.ndarray) -> np.ndarray:
        """Bilinear interpolation at arbitrary (line, pixel), clamped at the edges."""
        v = self.values[name]
        li = np.clip(np.searchsorted(self.lines, line) - 1, 0, len(self.lines) - 2)
        pi = np.clip(np.searchsorted(self.pixels, pixel) - 1, 0, len(self.pixels) - 2)

        l0, l1 = self.lines[li], self.lines[li + 1]
        p0, p1 = self.pixels[pi], self.pixels[pi + 1]
        tl = np.clip((line - l0) / np.maximum(l1 - l0, 1e-9), 0, 1)
        tp = np.clip((pixel - p0) / np.maximum(p1 - p0, 1e-9), 0, 1)

        v00, v01 = v[li, pi], v[li, pi + 1]
        v10, v11 = v[li + 1, pi], v[li + 1, pi + 1]
        return (
            v00 * (1 - tl) * (1 - tp)
            + v01 * (1 - tl) * tp
            + v10 * tl * (1 - tp)
            + v11 * tl * tp
        )
