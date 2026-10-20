"""Deterministic reflectance -> feature-vector transform.

The SAME transform is applied to the real EMIT pixels and to anything the NN
team trains on, so features never drift between dataset build and model use.

Feature groups (concatenated in this order):
  1. continuum-removed reflectance, one column per good band
  2. diagnostic absorption-feature depths (config.DIAGNOSTIC_FEATURES)
  3. SAM spectral angle (on continuum-removed spectra) to each endmember
  4. overall brightness (mean reflectance)
"""
import sys
from pathlib import Path

import numpy as np

_SERVICE_DIR = str(Path(__file__).resolve().parent.parent)
if _SERVICE_DIR not in sys.path:
    sys.path.insert(0, _SERVICE_DIR)

import config  # noqa: E402  (tests/reference/config.py stub)
import continuum  # noqa: E402
import sam  # noqa: E402


class FeatureExtractor:
    """Turn reflectance spectra on a fixed wavelength grid into feature vectors."""

    def __init__(self, wavelengths, endmembers, endmember_names, diagnostic_features=None):
        self.wl = np.asarray(wavelengths, dtype=float)
        self.endmembers = np.asarray(endmembers, dtype=float)
        self.endmember_names = list(endmember_names)
        self.features = diagnostic_features or config.DIAGNOSTIC_FEATURES
        if self.endmembers.shape != (len(self.endmember_names), self.wl.size):
            raise ValueError("endmembers shape must be (n_names, n_wavelengths)")
        # Continuum-removed endmembers, computed once, for SAM-angle features.
        self._em_cr = continuum.continuum_remove_many(self.wl, self.endmembers)[1]
        self._feature_order = list(self.features.keys())

    def feature_names(self):
        names = [f"cr_{w:.1f}nm" for w in self.wl]
        names += [f"depth_{k}" for k in self._feature_order]
        names += [f"sam_{n}" for n in self.endmember_names]
        names += ["brightness"]
        return names

    def transform(self, reflectance):
        """(n_pixels, n_bands) reflectance -> (n_pixels, n_features). NaN rows pass through."""
        r = np.atleast_2d(np.asarray(reflectance, dtype=float))
        if r.shape[1] != self.wl.size:
            raise ValueError(f"expected {self.wl.size} bands, got {r.shape[1]}")

        cr = continuum.continuum_remove_many(self.wl, r)[1]  # (n, nb)

        # Absorption depth per diagnostic window: 1 - min(continuum-removed) in band.
        depths = np.full((r.shape[0], len(self._feature_order)), np.nan)
        for j, key in enumerate(self._feature_order):
            lo, hi = self.features[key]["range"]
            win = (self.wl >= lo) & (self.wl <= hi)
            if win.any():
                with np.errstate(invalid="ignore"):
                    band_min = np.nanmin(cr[:, win], axis=1)
                depths[:, j] = np.clip(1.0 - band_min, 0.0, 1.0)

        # SAM angle (radians) on continuum-removed spectra to each endmember.
        angles = sam.spectral_angles(cr, self._em_cr)  # (n, n_em)

        brightness = np.nanmean(r, axis=1, keepdims=True)

        return np.hstack([cr, depths, angles, brightness])
