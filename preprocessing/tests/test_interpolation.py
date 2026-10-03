# =============================================================
# OWNER: ANANTAHANARAYANAN
# =============================================================
"""
Bad-band detection, spectral interpolation and smoothing.

Fixtures live in conftest.py.
"""

import numpy as np

import bad_bands
import interpolation
import smoothing


def test_interpolation_leaves_no_nan_and_keeps_good_bands(cube, wavelengths):
    """Good bands must pass through completely untouched."""
    mask = bad_bands.build_mask(wavelengths)
    out = interpolation.interpolate(cube, wavelengths, mask)

    assert np.isfinite(out).all()
    np.testing.assert_allclose(out[:, :, ~mask], cube[:, :, ~mask], rtol=1e-6)


def test_vectorised_interpolation_matches_numpy_interp(cube, wavelengths):
    """The vectorised path must equal the obvious per-pixel implementation."""
    mask = bad_bands.build_mask(wavelengths)
    out = interpolation.interpolate(cube, wavelengths, mask)

    expected = np.interp(wavelengths[mask], wavelengths[~mask], cube[3, 7, ~mask])
    np.testing.assert_allclose(out[3, 7, mask], expected, rtol=1e-5)


def test_interpolation_is_a_noop_when_nothing_is_flagged(cube, wavelengths):
    empty = np.zeros(len(wavelengths), dtype=bool)
    out = interpolation.interpolate(cube, wavelengths, empty)
    np.testing.assert_allclose(out, cube, rtol=1e-6)


# ── SNR-based rejection ─────────────────────────────────────────────────
def test_snr_rejection_flags_a_noisy_band(cube, wavelengths):
    """A band that is mostly sensor noise must be flagged and interpolated."""
    cube = cube.copy()
    rng = np.random.default_rng(seed=7)
    cube[:, :, 100] = rng.normal(0.3, 0.3, size=cube.shape[:2])

    clean = bad_bands.build_mask(wavelengths)
    assert not clean[100], "band 100 is not in a water window to begin with"

    assert bad_bands.snr_reject(cube, clean)[100]


def test_snr_rejection_keeps_good_bands(cube, wavelengths):
    """
    The estimator must not flag a healthy band just because the scene is
    spatially varied -- that variation is signal, not noise. Estimating
    noise from the spatial standard deviation would fail this test.
    """
    clean = bad_bands.build_mask(wavelengths)
    rejected = bad_bands.snr_reject(cube, clean)

    idx = int(np.argmin(np.abs(wavelengths - 2205.0)))   # Al-OH feature
    assert not rejected[idx]


def test_water_windows_flagged_but_network_bands_kept(wavelengths):
    """1400 / 1900 nm are flagged; 2205 (Al-OH) and 2350 (CO3) must not be."""
    mask = bad_bands.build_mask(wavelengths)
    near = lambda nm: int(np.argmin(np.abs(wavelengths - nm)))
    assert mask[near(1400)] and mask[near(1900)]
    assert not mask[near(2205)] and not mask[near(2350)]


# ── Smoothing ───────────────────────────────────────────────────────────
def test_vectorised_savgol_matches_per_pixel_loop(cube):
    """One SciPy call over the cube == the old per-pixel loop, just faster."""
    from scipy.signal import savgol_filter

    expected = np.zeros_like(cube)
    for i in range(cube.shape[0]):
        for j in range(cube.shape[1]):
            expected[i, j, :] = savgol_filter(cube[i, j, :], window_length=7,
                                              polyorder=2, mode="nearest")
    np.testing.assert_allclose(smoothing.savgol(cube), expected, atol=1e-5)


def test_savgol_skips_gracefully_when_too_few_bands():
    tiny = np.random.rand(4, 4, 3).astype(np.float32)
    assert smoothing.savgol(tiny).shape == tiny.shape
