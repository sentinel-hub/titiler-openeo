"""Tests for titiler.openeo.sar.calibration.

Uses a Grid2D with a single constant value per LUT so the coefficient maths
can be checked by hand, independent of Grid2D's own bilinear interpolation
(covered separately in test_sar_annotation.py).
"""

import numpy as np
import pytest

from titiler.openeo.sar.annotation import CalibrationLUT, Grid2D, NoiseLUT
from titiler.openeo.sar.calibration import calibrate
from titiler.openeo.sar.geocode import InverseMap

# Arbitrary, distinct constants -- not meant to satisfy the
# gamma/sigma incidence-angle identity, since that's not calibrate()'s concern.
SIGMA_NOUGHT = 2.0
BETA_NOUGHT = 4.0
GAMMA = 3.0


@pytest.fixture
def calibration_lut() -> CalibrationLUT:
    grid = Grid2D(
        lines=np.array([0.0, 10.0]),
        pixels=np.array([0.0, 10.0]),
        values={
            "sigmaNought": np.full((2, 2), SIGMA_NOUGHT),
            "betaNought": np.full((2, 2), BETA_NOUGHT),
            "gamma": np.full((2, 2), GAMMA),
        },
    )
    return CalibrationLUT(grid)


@pytest.fixture
def inverse() -> InverseMap:
    """Two pixels; coordinates only need to fall inside the LUT's grid."""
    return InverseMap(line=np.array([[1.0, 1.0]]), pixel=np.array([[1.0, 1.0]]))


def test_calibrate_null_coefficient_returns_dn_squared_uncalibrated(
    calibration_lut, inverse
):
    dn = np.array([[10.0, 0.0]])
    result = calibrate(dn, inverse, calibration_lut, coefficient=None)

    np.testing.assert_allclose(result.value, [[100.0, 0.0]])
    np.testing.assert_array_equal(result.valid_mask, [[True, False]])
    assert result.negative_count == 0


@pytest.mark.parametrize(
    "coefficient,expected_a",
    [
        ("beta0", BETA_NOUGHT),
        ("sigma0-ellipsoid", SIGMA_NOUGHT),
        ("gamma0-ellipsoid", GAMMA),
    ],
)
def test_calibrate_hand_computed(calibration_lut, inverse, coefficient, expected_a):
    dn = np.array([[10.0, 0.0]])
    result = calibrate(dn, inverse, calibration_lut, coefficient=coefficient)

    expected = np.array([[100.0 / expected_a**2, 0.0]])
    np.testing.assert_allclose(result.value, expected)
    np.testing.assert_array_equal(result.valid_mask, [[True, False]])


def test_calibrate_unsupported_coefficient_raises(calibration_lut, inverse):
    dn = np.array([[10.0]])
    inverse = InverseMap(line=np.array([[1.0]]), pixel=np.array([[1.0]]))
    with pytest.raises(ValueError, match="Unsupported calibration coefficient"):
        calibrate(dn, inverse, calibration_lut, coefficient="gamma0-terrain")


def test_calibrate_noise_removal_clamps_negatives_and_counts_them(
    calibration_lut, inverse
):
    # power = dn**2 = 50; noise = 80 (constant) -> power - eta = -30, clamped to 0.
    noise = NoiseLUT(
        range_grid=Grid2D(
            lines=np.array([0.0, 10.0]),
            pixels=np.array([0.0, 10.0]),
            values={"noiseRangeLut": np.full((2, 2), 80.0)},
        )
    )
    dn = np.array([[np.sqrt(50.0), 0.0]])
    result = calibrate(dn, inverse, calibration_lut, coefficient=None, noise=noise)

    np.testing.assert_allclose(result.value, [[0.0, 0.0]])
    # Only the first pixel is both valid (dn > 0) and went negative.
    assert result.negative_count == 1


def test_calibrate_noise_removal_leaves_positive_values_unclamped(
    calibration_lut, inverse
):
    # power = dn**2 = 200; noise = 80 -> 120, still positive.
    noise = NoiseLUT(
        range_grid=Grid2D(
            lines=np.array([0.0, 10.0]),
            pixels=np.array([0.0, 10.0]),
            values={"noiseRangeLut": np.full((2, 2), 80.0)},
        )
    )
    dn = np.array([[np.sqrt(200.0), 0.0]])
    result = calibrate(dn, inverse, calibration_lut, coefficient=None, noise=noise)

    np.testing.assert_allclose(result.value, [[120.0, 0.0]])
    assert result.negative_count == 0


def test_calibrate_without_noise_skips_subtraction(calibration_lut, inverse):
    dn = np.array([[10.0, 0.0]])
    result = calibrate(dn, inverse, calibration_lut, coefficient=None, noise=None)

    np.testing.assert_allclose(result.value, [[100.0, 0.0]])
    assert result.negative_count == 0
