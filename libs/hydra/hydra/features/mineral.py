"""
Reflectance -> the 275-feature mineral vector.

Same transform as services/mineral_analysis/rtm_inversion/features.py, which
built training_sample.csv and dataset_sample.csv (verified to 5e-7):

    255 continuum-removed good bands | 7 absorption depths |
    12 SAM angles to the continuum-removed USGS library | brightness
"""

import warnings

import numpy as np

from .. import spec
from ..classical.library import aligned_library
from .continuum import continuum_remove_many


def spectral_angles(pixels, references):
    """(n, b) x (k, b) -> (n, k) radians; NaN for rows that are non-finite or zero."""
    def unit(a):
        finite = np.isfinite(a).all(axis=1)
        clean = np.where(finite[:, None], a, 0.0)
        norms = np.linalg.norm(clean, axis=1)
        ok = finite & (norms > 0)
        u = np.zeros_like(clean)
        u[ok] = clean[ok] / norms[ok, None]
        return u, ok

    pu, pok = unit(np.atleast_2d(pixels))
    ru, rok = unit(np.atleast_2d(references))
    ang = np.arccos(np.clip(pu @ ru.T, -1.0, 1.0))
    ang[~pok, :] = np.nan
    ang[:, ~rok] = np.nan
    return ang


class MineralFeatures:
    """Feature extractor on the EMIT good-band grid."""

    def __init__(self):
        self.wavelengths = spec.emit_good_wavelengths()
        self.names, self.endmembers = aligned_library(self.wavelengths)
        self.endmembers_cr = continuum_remove_many(self.wavelengths, self.endmembers)
        self._windows = [(self.wavelengths >= lo) & (self.wavelengths <= hi)
                         for lo, hi in spec.DIAGNOSTIC_FEATURES.values()]

    def transform(self, reflectance, return_cr=False):
        """(n, 255) reflectance on the good-band grid -> (n, 275) float32."""
        r = np.atleast_2d(np.asarray(reflectance, dtype=np.float64))
        if r.shape[1] != self.wavelengths.size:
            raise ValueError(f"expected {self.wavelengths.size} bands, got {r.shape[1]}")
        cr = continuum_remove_many(self.wavelengths, r)
        depths = np.empty((r.shape[0], len(self._windows)))
        with np.errstate(invalid="ignore"), warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)    # all-NaN rows
            for j, win in enumerate(self._windows):
                depths[:, j] = np.clip(1.0 - np.nanmin(cr[:, win], axis=1), 0.0, 1.0)
            brightness = np.nanmean(r, axis=1, keepdims=True)
        angles = spectral_angles(cr, self.endmembers_cr)
        x = np.hstack([cr, depths, angles, brightness]).astype(np.float32)
        return (x, cr) if return_cr else x


def to_good_bands(spectra, wavelengths, max_gap_nm=4.0):
    """
    Put sensor spectra onto the EMIT good-band grid.

    EMIT itself: exact band picks. Another sensor: linear interpolation, refused
    where the nearest sensor band is more than max_gap_nm away (no invention).
    """
    target = spec.emit_good_wavelengths()
    wl = np.asarray(wavelengths, dtype=float)
    s = np.atleast_2d(np.asarray(spectra, dtype=np.float64))
    nearest = np.abs(wl[None, :] - target[:, None]).argmin(axis=1)
    gap = np.abs(wl[nearest] - target)
    if np.all(gap < 0.05):
        return s[:, nearest]
    if gap.max() > max_gap_nm:
        raise ValueError(f"sensor lacks bands near {target[gap > max_gap_nm][:5]} nm")
    return np.vstack([np.interp(target, wl, row) for row in s])
