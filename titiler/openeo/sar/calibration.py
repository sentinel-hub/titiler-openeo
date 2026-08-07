"""Sentinel-1 GRD radiometric calibration: DN -> physical backscatter.

Combines the DN read by `RasterStack` through the normal `load_collection` path with
the calibration constant `A` and noise `eta`, both now read as ordinary cube bands
(band-source readers, docs/adr/0002-band-sources.md) rather than fetched and
interpolated here. See ADR 0001 S7.3 steps 4-8 for the physics; issue #348 / ADR 0002
S2.6 for why this reduces to arithmetic.
"""

from dataclasses import dataclass
from typing import Optional

import numpy as np

__all__ = ["CalibrationResult", "calibrate"]


@dataclass(frozen=True)
class CalibrationResult:
    """The calibrated backscatter value plus the diagnostics callers need."""

    #: Linear-scale backscatter (or DN^2 if `a` is None), float64, HxW.
    value: np.ndarray
    #: True where source DN > 0, i.e. not border/no-data, HxW.
    valid_mask: np.ndarray
    #: Count of valid pixels where noise subtraction went negative before clamping.
    negative_count: int


def calibrate(
    dn: np.ndarray,
    a: Optional[np.ndarray] = None,
    eta: Optional[np.ndarray] = None,
) -> CalibrationResult:
    """Compute `(DN^2 - eta) / A^2`.

    `a=None` returns `DN^2` (or `DN^2 - eta`) uncalibrated (openEO's
    `coefficient=null`). `eta=None` skips noise subtraction
    (`noise_removal=false`). Both, when given, are per-pixel arrays already
    evaluated on this read's destination grid -- by `CalibrationBandReader`/
    `NoiseBandReader` (`bandsources/readers.py`) for the common case, or by
    whatever else put a `{pol}_<suffix>_lut`/`{pol}_noise_lut` band on the
    cube; this function has no opinion on their source.

    Noise subtraction can drive values negative in low-backscatter areas;
    those are clamped to 0 and counted (SNAP does the same) rather than
    emitted as negative backscatter.
    """
    valid_mask = dn > 0
    power = dn.astype("f8") ** 2

    negative_count = 0
    if eta is not None:
        negative = (power - eta) < 0
        power = np.maximum(power - eta, 0.0)
        negative_count = int(np.count_nonzero(negative & valid_mask))

    value = power if a is None else power / (a.astype("f8") ** 2)

    return CalibrationResult(
        value=value, valid_mask=valid_mask, negative_count=negative_count
    )
