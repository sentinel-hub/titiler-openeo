"""Sentinel-1 GRD radiometric calibration: DN -> physical backscatter.

Combines the DN read by `RasterStack` through the normal `load_collection` path (the
read itself is out of scope here -- see geocode.py and
docs/adr/0001-sar-backscatter.md S7.3) with the calibration/noise LUTs (annotation.py),
evaluated at the geocode.py inverse-mapped (line, pixel) coordinates, into the
coefficient the caller asked for. See ADR S7.3 steps 4-8.
"""

from dataclasses import dataclass
from typing import Optional

import numpy as np

from .annotation import COEFFICIENT_LUT, CalibrationLUT, NoiseLUT
from .geocode import InverseMap

__all__ = ["CalibrationResult", "calibrate"]


@dataclass(frozen=True)
class CalibrationResult:
    """The calibrated backscatter value plus the diagnostics callers need."""

    #: Linear-scale backscatter (or DN^2 if coefficient is None), float64, HxW.
    value: np.ndarray
    #: True where source DN > 0, i.e. not border/no-data, HxW.
    valid_mask: np.ndarray
    #: Count of valid pixels where noise subtraction went negative before clamping.
    negative_count: int


def calibrate(
    dn: np.ndarray,
    inverse: InverseMap,
    calibration: CalibrationLUT,
    coefficient: Optional[str],
    noise: Optional[NoiseLUT] = None,
) -> CalibrationResult:
    """Compute `(DN^2 - noise) / A^2` for `coefficient`.

    `coefficient=None` returns `DN^2` uncalibrated (openEO's `null`).
    `noise=None` skips noise subtraction (`noise_removal=false`). `A` is
    selected via `COEFFICIENT_LUT`, so `coefficient` must be one of openEO's
    own names (`beta0`, `sigma0-ellipsoid`, `gamma0-ellipsoid`) or `None`.

    Noise subtraction can drive values negative in low-backscatter areas;
    those are clamped to 0 and counted (SNAP does the same) rather than
    emitted as negative backscatter.
    """
    valid_mask = dn > 0
    power = dn.astype("f8") ** 2

    negative_count = 0
    if noise is not None:
        eta = noise.evaluate(inverse.line, inverse.pixel)
        negative = (power - eta) < 0
        power = np.maximum(power - eta, 0.0)
        negative_count = int(np.count_nonzero(negative & valid_mask))

    if coefficient is None:
        value = power
    else:
        lut_name = COEFFICIENT_LUT.get(coefficient)
        if lut_name is None:
            raise ValueError(
                f"Unsupported calibration coefficient: {coefficient!r}. "
                f"Expected one of {sorted(COEFFICIENT_LUT)} or None."
            )
        a = calibration.grid.interp(lut_name, inverse.line, inverse.pixel)
        value = power / (a**2)

    return CalibrationResult(
        value=value, valid_mask=valid_mask, negative_count=negative_count
    )
