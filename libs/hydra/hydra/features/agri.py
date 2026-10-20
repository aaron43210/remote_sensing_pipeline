"""Reflectance cube -> the agriculture input vector (spec.AGRI_BANDS_NM)."""

import numpy as np

from .. import spec


def interp_weights(wavelengths, targets=spec.AGRI_BANDS_NM, max_gap=spec.AGRI_MAX_BAND_GAP_NM):
    """
    Linear interpolation of the sensor spectrum at the exact target wavelengths.

    The LUT and the network are built at the targets themselves; taking the
    nearest sensor band instead (up to 3.7 nm away on EMIT) shifts the red edge
    enough to move LAI. Refuses a target the sensor does not cover.
    """
    wl = np.asarray(wavelengths, dtype=float)
    t = np.asarray(targets, dtype=float)
    nearest = np.abs(wl[None, :] - t[:, None]).min(axis=1)
    if nearest.max() > max_gap:
        raise ValueError(f"no sensor band within {max_gap} nm of {list(t[nearest > max_gap])}")
    hi = np.clip(np.searchsorted(wl, t), 1, wl.size - 1)
    lo = hi - 1
    w = np.clip((t - wl[lo]) / (wl[hi] - wl[lo]), 0.0, 1.0)
    return lo, hi, w


def select(spectra, wavelengths):
    """(n, bands) -> (n, len(AGRI_BANDS_NM)) float32."""
    s = np.asarray(spectra, dtype=np.float32)
    lo, hi, w = interp_weights(wavelengths)
    return (s[:, lo] * (1 - w) + s[:, hi] * w).astype(np.float32)


def dataset_columns():
    """Column names of the training CSV that correspond to AGRI_BANDS_NM."""
    return [f"Reflectance_{b:.2f}_nm" for b in spec.AGRI_BANDS_NM]
