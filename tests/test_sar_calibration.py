"""Tests for titiler.openeo.sar.calibration.

`calibrate()` is pure arithmetic (issue #348 / ADR 0002 increment 6): `a`/`eta`
are plain per-pixel arrays, not LUT objects to interpolate -- so these tests
pass hand-picked constants directly, independent of how a real `a`/`eta`
array would have been produced.
"""

import numpy as np

from titiler.openeo.sar.calibration import calibrate

SIGMA_NOUGHT = 2.0


def test_calibrate_null_coefficient_returns_dn_squared_uncalibrated():
    dn = np.array([[10.0, 0.0]])
    result = calibrate(dn)

    np.testing.assert_allclose(result.value, [[100.0, 0.0]])
    np.testing.assert_array_equal(result.valid_mask, [[True, False]])
    assert result.negative_count == 0


def test_calibrate_hand_computed():
    dn = np.array([[10.0, 0.0]])
    a = np.full_like(dn, SIGMA_NOUGHT)
    result = calibrate(dn, a=a)

    expected = np.array([[100.0 / SIGMA_NOUGHT**2, 0.0]])
    np.testing.assert_allclose(result.value, expected)
    np.testing.assert_array_equal(result.valid_mask, [[True, False]])


def test_calibrate_noise_removal_clamps_negatives_and_counts_them():
    # power = dn**2 = 50; eta = 80 (constant) -> power - eta = -30, clamped to 0.
    dn = np.array([[np.sqrt(50.0), 0.0]])
    eta = np.full_like(dn, 80.0)
    result = calibrate(dn, eta=eta)

    np.testing.assert_allclose(result.value, [[0.0, 0.0]])
    # Only the first pixel is both valid (dn > 0) and went negative.
    assert result.negative_count == 1


def test_calibrate_noise_removal_leaves_positive_values_unclamped():
    # power = dn**2 = 200; eta = 80 -> 120, still positive.
    dn = np.array([[np.sqrt(200.0), 0.0]])
    eta = np.full_like(dn, 80.0)
    result = calibrate(dn, eta=eta)

    np.testing.assert_allclose(result.value, [[120.0, 0.0]])
    assert result.negative_count == 0


def test_calibrate_without_noise_skips_subtraction():
    dn = np.array([[10.0, 0.0]])
    result = calibrate(dn, eta=None)

    np.testing.assert_allclose(result.value, [[100.0, 0.0]])
    assert result.negative_count == 0
