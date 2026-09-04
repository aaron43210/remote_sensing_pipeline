# =============================================================
# OWNER: ANANTHAN S & HARIKRISHNAN
# =============================================================
"""
Continuum Removal for Mineral Identification
"""
import numpy as np
from scipy.spatial import ConvexHull
import logging

logger = logging.getLogger(__name__)

class ContinuumRemover:
    def __init__(self, method="hull"):
        self.method = method

    def remove(self, wavelengths, spectrum):
        wvl = np.asarray(wavelengths, dtype=float)
        spec = np.asarray(spectrum, dtype=float)

        if self.method == "hull":
            return self._hull_removal(wvl, spec)
        else:
            return self._linear_removal(wvl, spec)

    def _hull_removal(self, wvl, spec):
        points = np.column_stack([wvl, spec])
        n = len(wvl)
        augmented = np.vstack([[wvl[0], 0], points, [wvl[-1], 0]])

        try:
            hull = ConvexHull(augmented)
        except Exception:
            logger.warning("Convex hull failed, using linear fallback")
            return self._linear_removal(wvl, spec)

        hull_vertices = hull.vertices
        hull_points = augmented[hull_vertices]
        sort_idx = np.argsort(hull_points[:, 0])
        hull_points = hull_points[sort_idx]

        unique_wvl = []
        unique_refl = []
        for wp in hull_points:
            if len(unique_wvl) == 0 or wp[0] != unique_wvl[-1]:
                unique_wvl.append(wp[0])
                unique_refl.append(wp[1])
            else:
                if wp[1] > unique_refl[-1]:
                    unique_refl[-1] = wp[1]

        if len(unique_wvl) < 2:
            logger.warning("Not enough hull points, using linear fallback")
            return self._linear_removal(wvl, spec)

        continuum = np.interp(wvl, unique_wvl, unique_refl)
        continuum = np.maximum(continuum, spec)
        cr_spectrum = spec / (continuum + 1e-10)
        cr_spectrum = np.clip(cr_spectrum, 0, 1)

        return cr_spectrum, continuum

    def _linear_removal(self, wvl, spec):
        continuum = np.linspace(spec[0], spec[-1], len(spec))
        continuum = np.maximum(continuum, spec)
        cr_spectrum = spec / (continuum + 1e-10)
        cr_spectrum = np.clip(cr_spectrum, 0, 1)
        return cr_spectrum, continuum

    def remove_batch(self, wavelengths, spectra_batch):
        n_pixels = spectra_batch.shape[0]
        n_bands = spectra_batch.shape[1]
        cr_batch = np.zeros_like(spectra_batch)
        cont_batch = np.zeros_like(spectra_batch)

        for i in range(n_pixels):
            cr, cont = self.remove(wavelengths, spectra_batch[i])
            cr_batch[i] = cr
            cont_batch[i] = cont

        return cr_batch, cont_batch
