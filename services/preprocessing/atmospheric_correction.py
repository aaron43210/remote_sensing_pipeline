# =============================================================
# OWNER: ANANTAHANARAYANAN
# =============================================================
import numpy as np

def quac_correction(cube):
    """
    Quick Atmospheric Correction (QUAC) for hyperspectral cubes.
    Based on scene statistics (dark pixel subtraction).
    """
    # Estimate path radiance as the 1% lowest reflectance per band
    dark = np.percentile(cube, 1, axis=(0, 1))
    corrected = cube - dark
    corrected = np.clip(corrected, 0, 1)
    return corrected

def get_good_bands_indices(wavelengths):
    """
    Get indices of bands outside water absorption windows:
    ~1400nm, ~1900nm, >2400nm
    """
    good = []
    for i, wl in enumerate(wavelengths):
        if (wl < 1300) or (1500 < wl < 1800) or (2000 < wl < 2400):
            good.append(i)
    return good, [wavelengths[i] for i in good]

def apply_bad_bands_filter(cube, indices):
    """
    Apply band filtering to a datacube.
    """
    return cube[:, :, indices]

def apply_savitzky_golay(cube, window=7, polyorder=2):
    from scipy.signal import savgol_filter
    rows, cols, bands = cube.shape
    out = np.zeros_like(cube)
    for i in range(rows):
        for j in range(cols):
            out[i, j, :] = savgol_filter(
                cube[i, j, :],
                window_length=window,
                polyorder=polyorder,
                mode='nearest'
            )
    return out
