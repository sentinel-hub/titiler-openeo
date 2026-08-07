"""Sentinel-1 GRD calibration and thermal-noise annotation parsing.

Parses the ESA-supplied calibration and thermal-noise Look-Up Tables (LUTs)
that ship alongside every Sentinel-1 GRD measurement asset, and evaluates them
at arbitrary (line, pixel) coordinates via bilinear interpolation.

See docs/adr/0001-sar-backscatter.md S1.6d, S1.6g and S9.4 for the
radiometric background this implements, in particular:

* The absolute calibration constant K is already folded into the LUTs by ESA
  (`A = sqrt(A_dn^2 * K / f(incidence_angle))`) and must NOT be applied
  again -- K is not 1 for S1B (1.393) or S1C (0.8995).
* The noise annotation schema changed at IPF 2.90 (2018-03): older products
  use a single `noiseVectorList`, newer ones use `noiseRangeVectorList` +
  `noiseAzimuthVectorList`. Both are parsed here.
* Vectors within one annotation are not guaranteed to sample the range axis
  at identical pixel positions -- confirmed against genuine, un-trimmed CDSE
  data, where the sampled pixel index drifts by a few samples line to line
  and the vector length can even differ by one. Every vector is resampled
  onto a common pixel axis (`_resample_row`) before being stacked into a
  `Grid2D`; naively assuming a shared axis silently misaligns values.

XML is parsed with `defusedxml` rather than the stdlib `xml.etree`, which was
measured expanding a billion-laughs payload on Python 3.13.1 (ADR S9.2).
"""

from dataclasses import dataclass, field
from threading import Condition
from typing import Dict, List, Optional
from xml.etree.ElementTree import Element

import numpy as np
from cachetools import LRUCache, cached
from cachetools.keys import hashkey
from defusedxml import ElementTree as ET

from ..settings import SARSettings
from .fetcher import AssetFetcher, get_default_fetcher

__all__ = [
    "Grid2D",
    "CalibrationLUT",
    "NoiseLUT",
    "COEFFICIENT_LUT",
    "parse_calibration",
    "parse_noise",
    "get_calibration",
    "get_noise",
]

_settings = SARSettings()


def _text(el: Element, tag: str) -> str:
    """Required child text, or a clear error naming the missing element."""
    value = el.findtext(tag)
    if value is None:
        raise ValueError(f"Sentinel-1 annotation element is missing <{tag}>")
    return value


def _floats(el: Element, tag: str) -> np.ndarray:
    return np.array(_text(el, tag).split(), dtype="f8")


def _resample_row(
    canonical_pixels: np.ndarray, own_pixels: np.ndarray, own_values: np.ndarray
) -> np.ndarray:
    """Resample one calibration/noise vector's LUT column onto a shared pixel axis.

    ESA does not guarantee that every vector in an annotation samples the
    range axis at identical pixel positions. Real CDSE products drift by a
    handful of samples line to line (confirmed against genuine, un-trimmed
    ESA data), and the sample *count* can even differ by one between
    vectors. Stacking raw rows under the assumption that they share
    vectors[0]'s pixel axis silently misaligns values -- interpolating each
    row onto a common axis first is what makes that assumption safe.
    """
    if own_values.shape != own_pixels.shape:
        raise ValueError(
            f"Ragged LUT vector: {len(own_values)} values but "
            f"{len(own_pixels)} pixel positions"
        )
    return np.interp(canonical_pixels, own_pixels, own_values)


@dataclass(frozen=True)
class Grid2D:
    """A LUT sampled on a rectilinear (line, pixel) grid, bilinearly interpolable."""

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


@dataclass(frozen=True)
class CalibrationLUT:
    """Parsed `calibration-*.xml`: sigma0/beta0/gamma calibration vectors."""

    grid: Grid2D

    def sigma_nought(self, line: np.ndarray, pixel: np.ndarray) -> np.ndarray:
        """Radar cross-section (sigma0) calibration LUT, interpolated."""
        return self.grid.interp("sigmaNought", line, pixel)

    def beta_nought(self, line: np.ndarray, pixel: np.ndarray) -> np.ndarray:
        """Radar brightness (beta0) calibration LUT, interpolated."""
        return self.grid.interp("betaNought", line, pixel)

    def gamma(self, line: np.ndarray, pixel: np.ndarray) -> np.ndarray:
        """Gamma calibration LUT, interpolated."""
        return self.grid.interp("gamma", line, pixel)

    def dn(self, line: np.ndarray, pixel: np.ndarray) -> np.ndarray:
        """Raw DN calibration LUT, interpolated (rarely used directly --
        `sigma_nought`/`beta_nought`/`gamma` already have K folded in, per
        the module docstring; exposed for parity with the other three
        vectors this same annotation carries)."""
        return self.grid.interp("dn", line, pixel)

    def ellipsoid_incidence_angle(
        self, line: np.ndarray, pixel: np.ndarray
    ) -> np.ndarray:
        """Ellipsoid incidence angle in degrees, recovered from the LUTs alone.

        theta = arccos((A_gamma / A_sigma)^2); no orbit geometry is needed
        (ADR S1.6d). ESA's own calibration note confirms this identity:
        cos(alpha) = A_gamma^2 / A_sigma^2.
        """
        a_s = self.sigma_nought(line, pixel)
        a_g = self.gamma(line, pixel)
        ratio = np.clip((a_g / a_s) ** 2, -1.0, 1.0)
        return np.degrees(np.arccos(ratio))


#: openEO `sar_backscatter` coefficient name -> calibration LUT array name.
COEFFICIENT_LUT: Dict[str, str] = {
    "beta0": "betaNought",
    "sigma0-ellipsoid": "sigmaNought",
    "gamma0-ellipsoid": "gamma",
}


def parse_calibration(xml: bytes) -> CalibrationLUT:
    """Parse a Sentinel-1 `calibration-*.xml` annotation.

    Deliberately does not read or apply `absoluteCalibrationConstant` -- it is
    already folded into every vector below (ADR S9.4).
    """
    root = ET.fromstring(xml)
    vectors = root.findall("calibrationVectorList/calibrationVector")
    if not vectors:
        raise ValueError("No calibrationVector found in calibration annotation")

    lines = np.array([float(_text(v, "line")) for v in vectors])
    pixels = _floats(vectors[0], "pixel")
    per_vector_pixels = [_floats(v, "pixel") for v in vectors]

    values = {}
    for name in ("sigmaNought", "betaNought", "gamma", "dn"):
        rows = [
            _resample_row(pixels, own_pixels, _floats(v, name))
            for v, own_pixels in zip(vectors, per_vector_pixels)
        ]
        values[name] = np.vstack(rows)
    return CalibrationLUT(Grid2D(lines, pixels, values))


@dataclass(frozen=True)
class _AzimuthBlock:
    """One per-swath noise-descalloping block (modern IPF schema only)."""

    first_line: float
    last_line: float
    first_sample: float
    last_sample: float
    lines: np.ndarray
    lut: np.ndarray


@dataclass(frozen=True)
class NoiseLUT:
    """Parsed `noise-*.xml`: thermal noise in DN^2, either ESA schema generation."""

    range_grid: Grid2D
    azimuth_blocks: List[_AzimuthBlock] = field(default_factory=list)

    def evaluate(self, line: np.ndarray, pixel: np.ndarray) -> np.ndarray:
        """Full thermal noise in DN^2: range LUT scaled by the per-swath azimuth LUT.

        Legacy (IPF < 2.90) products have no azimuth blocks; the range LUT is
        the complete noise estimate in that case.
        """
        noise = self.range_grid.interp("noiseRangeLut", line, pixel)
        if not self.azimuth_blocks:
            return noise

        scale = np.ones_like(noise)
        for blk in self.azimuth_blocks:
            sel = (
                (line >= blk.first_line)
                & (line <= blk.last_line)
                & (pixel >= blk.first_sample)
                & (pixel <= blk.last_sample)
            )
            if not sel.any():
                continue
            scale[sel] = np.interp(line[sel], blk.lines, blk.lut)
        return noise * scale


def parse_noise(xml: bytes) -> NoiseLUT:
    """Parse a Sentinel-1 `noise-*.xml` annotation, handling both ESA schema generations.

    IPF >= 2.90 (2018-03 onward) uses `noiseRangeVectorList` +
    `noiseAzimuthVectorList`. IPF < 2.90 uses a single `noiseVectorList` with
    no azimuth descalloping vector at all. Both occur in the CDSE archive --
    a parser written against only the modern layout fails on every product
    before March 2018, 3.5 years of archive (ADR S1.6g).
    """
    root = ET.fromstring(xml)
    vectors = root.findall("noiseRangeVectorList/noiseRangeVector")
    lut_tag = "noiseRangeLut"
    if not vectors:
        vectors = root.findall("noiseVectorList/noiseVector")
        lut_tag = "noiseLut"
    if not vectors:
        raise ValueError(
            "Noise annotation has neither noiseRangeVectorList nor noiseVectorList"
        )

    lines = np.array([float(_text(v, "line")) for v in vectors])
    pixels = _floats(vectors[0], "pixel")
    rows = [
        _resample_row(pixels, _floats(v, "pixel"), _floats(v, lut_tag)) for v in vectors
    ]
    range_grid = Grid2D(lines, pixels, {"noiseRangeLut": np.vstack(rows)})

    blocks = []
    for v in root.findall("noiseAzimuthVectorList/noiseAzimuthVector"):
        lut_text = v.findtext("noiseAzimuthLut")
        if not lut_text:
            continue
        blocks.append(
            _AzimuthBlock(
                first_line=float(_text(v, "firstAzimuthLine")),
                last_line=float(_text(v, "lastAzimuthLine")),
                first_sample=float(_text(v, "firstRangeSample")),
                last_sample=float(_text(v, "lastRangeSample")),
                lines=_floats(v, "line"),
                lut=np.array(lut_text.split(), dtype="f8"),
            )
        )
    return NoiseLUT(range_grid, blocks)


# Thread-safe caches keyed on href, storing the *parsed* LUTs rather than the
# raw XML bytes: a parsed set is ~100 KB vs ~1-1.5 MB of XML per polarisation
# (ADR S7.5), and a href never changes its content once published. RasterStack
# executes tasks on a thread pool, so these must be safe under concurrent use.
#
# cachetools' plain `lock=` only guards the cache dict during lookup/store; it
# does not stop N concurrent misses from all calling the wrapped function
# before the first one finishes (`lock=` is released between the miss check
# and the call -- confirmed against cachetools 7.0.5's own source). `cached`
# has a `condition=` parameter specifically for this: it tracks in-flight keys
# and blocks other callers on them until the first caller stores a result, so
# only one fetch ever happens per href even under concurrent access.
_calibration_cache: LRUCache = LRUCache(maxsize=_settings.annotation_cache_maxsize)
_noise_cache: LRUCache = LRUCache(maxsize=_settings.annotation_cache_maxsize)
_calibration_cache_condition = Condition()
_noise_cache_condition = Condition()


@cached(
    _calibration_cache,
    key=lambda href, fetcher=None: hashkey(href),
    condition=_calibration_cache_condition,
)
def get_calibration(
    href: str, fetcher: Optional[AssetFetcher] = None
) -> CalibrationLUT:
    """Fetch and parse a calibration annotation, cached by href."""
    fetcher = fetcher or get_default_fetcher()
    return parse_calibration(fetcher.fetch(href))


@cached(
    _noise_cache,
    key=lambda href, fetcher=None: hashkey(href),
    condition=_noise_cache_condition,
)
def get_noise(href: str, fetcher: Optional[AssetFetcher] = None) -> NoiseLUT:
    """Fetch and parse a noise annotation, cached by href."""
    fetcher = fetcher or get_default_fetcher()
    return parse_noise(fetcher.fetch(href))
