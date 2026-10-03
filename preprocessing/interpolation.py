# =============================================================
# OWNER: ANANTAHANARAYANAN
# =============================================================
"""
Fill flagged bands by interpolating along the spectral axis.

Paired with bad_bands.py, which decides WHICH bands are bad; this module
decides what to put in their place.

WHY INTERPOLATE INSTEAD OF DELETE
    Deleting bad bands changes the band count and shifts every index after
    them, and the network's 1450 nm input sits INSIDE the 1340-1460 nm water
    window: deleted, it would silently be replaced by whatever band is
    nearest, ~1465 nm or ~1300 nm. Interpolated, it is a smooth estimate from
    both sides, and the message lists it under 'interpolated_bands' so the
    model team knows that input is estimated, not measured.

    Smoothing also needs a continuous spectrum: a hole would make the
    Savitzky-Golay window straddle a gap.
"""

import logging
import numpy as np

logger = logging.getLogger(__name__)


def _weights(wl, mask):
    """
    For each bad band, find the two good bands to blend and the blend weight.

    These depend only on the wavelength axis, never on pixel values, so we
    compute them once per tile and apply them to every pixel at once.
    """
    good_idx = np.flatnonzero(~mask)
    good_wl = wl[good_idx]

    # Where each bad wavelength falls within the surviving good axis.
    hi = np.searchsorted(good_wl, wl[mask]).clip(1, len(good_wl) - 1)
    lo = hi - 1

    span = good_wl[hi] - good_wl[lo]
    w = (wl[mask] - good_wl[lo]) / np.where(span > 0, span, 1.0)

    # Clipping reproduces np.interp's flat extrapolation past either end.
    return good_idx[lo], good_idx[hi], w.clip(0.0, 1.0).astype(np.float32)


def interpolate(cube, wavelengths, mask):
    """
    Linearly interpolate across every flagged band.

    Band count and wavelength axis are unchanged. Fully vectorised -- no
    per-pixel Python loop.

    Args:
        cube: (rows, cols, bands) float array
        wavelengths: sequence of length n_bands
        mask: boolean array from bad_bands, True = bad

    Returns:
        (rows, cols, bands) float32 array with no flagged bands remaining.
    """
    out = cube.astype(np.float32, copy=True)

    if not mask.any():
        return out

    if (~mask).sum() < 2:
        # Cannot interpolate from fewer than two anchors; the quality gate
        # flags this scene rather than us returning nonsense.
        logger.error("Fewer than 2 good bands -- skipping interpolation")
        return out

    lo, hi, w = _weights(np.asarray(wavelengths, dtype=float), mask)
    out[:, :, mask] = out[:, :, lo] * (1.0 - w) + out[:, :, hi] * w
    return out
