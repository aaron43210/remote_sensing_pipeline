"""Continuum removal module compatibility wrapper."""
import numpy as np
try:
    from services.mineral_analysis import continuum
except ImportError:
    try:
        from . import continuum
    except ImportError:
        import continuum



class ContinuumRemover:
    def __init__(self, method="hull"):
        self.method = method

    def remove(self, wavelengths, spectrum):
        w = np.asarray(wavelengths, dtype=float)
        r = np.asarray(spectrum, dtype=float)
        cont = continuum.compute_continuum(w, r)
        cr = continuum._divide(r, cont)
        return cr, cont
