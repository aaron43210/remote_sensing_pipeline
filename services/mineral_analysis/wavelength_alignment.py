# =============================================================
# OWNER: ANANTHAN S & HARIKRISHNAN
# =============================================================
"""
Wavelength Alignment Module
"""
import numpy as np
from scipy.interpolate import interp1d
import logging

logger = logging.getLogger(__name__)

class WavelengthAligner:
    def __init__(self, method="cubic"):
        self.method = method

    def align(self, library_wavelengths, library_spectrum, sensor_wavelengths):
        lib_wvl = np.asarray(library_wavelengths, dtype=float)
        lib_spec = np.asarray(library_spectrum, dtype=float)
        sen_wvl = np.asarray(sensor_wavelengths, dtype=float)

        overlap_min = max(lib_wvl.min(), sen_wvl.min())
        overlap_max = min(lib_wvl.max(), sen_wvl.max())

        if overlap_min >= overlap_max:
            logger.error("No wavelength overlap between library and sensor")
            return np.zeros(len(sen_wvl))

        kind = "cubic" if self.method in ["cubic", "spline"] else "linear"

        interpolator = interp1d(
            lib_wvl, lib_spec,
            kind=kind,
            bounds_error=False,
            fill_value="extrapolate"
        )

        aligned = interpolator(sen_wvl)
        aligned = np.clip(aligned, 0.0, 1.0)
        return aligned

    def align_library(self, library, sensor_wavelengths):
        aligned = {}
        for name, spectrum in library.minerals.items():
            aligned[name] = self.align(library.wavelengths, spectrum, sensor_wavelengths)
        logger.info(f"Aligned {len(aligned)} minerals to sensor wavelengths")
        return aligned
