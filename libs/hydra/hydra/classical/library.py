"""USGS reference library: load the 12 CSVs and align them to a wavelength grid."""

from functools import lru_cache

import numpy as np

from .. import spec


@lru_cache(maxsize=1)
def load_library():
    """Returns (names, wavelengths_nm (441,), spectra (12, 441)) in spec.MINERALS order."""
    spectra, wl = [], None
    for name in spec.MINERALS:
        data = np.loadtxt(spec.USGS_LIBRARY_DIR / f"{name}.csv", delimiter=",", skiprows=3)
        if wl is None:
            wl = data[:, 0]
        elif not np.allclose(wl, data[:, 0]):
            raise ValueError(f"{name}: wavelength grid differs from the other minerals")
        spectra.append(data[:, 1])
    return list(spec.MINERALS), wl, np.asarray(spectra, dtype=float)


def aligned_library(target_wavelengths):
    """Library resampled (linear, no extrapolation) onto target wavelengths -> (12, n)."""
    names, wl, spectra = load_library()
    t = np.asarray(target_wavelengths, dtype=float)
    if t.min() < wl.min() or t.max() > wl.max():
        raise ValueError(f"targets {t.min():.1f}-{t.max():.1f} nm outside library "
                         f"{wl.min():.1f}-{wl.max():.1f} nm")
    return names, np.vstack([np.interp(t, wl, s) for s in spectra])
