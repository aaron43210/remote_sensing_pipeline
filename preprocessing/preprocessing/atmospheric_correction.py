# =============================================================
# OWNER: ANANTAHANARAYANAN
# =============================================================
import numpy as np
import xarray as xr


def quac_correction_lazy(cube: xr.DataArray) -> xr.DataArray:
    """
    Quick Atmospheric Correction (QUAC) — dark pixel subtraction.

    Estimates path radiance as the 1st percentile of reflectance per band
    across the entire scene, then subtracts it. Vectorized across the full
    Dask graph — no pixel-by-pixel loop.

    Args:
        cube: xr.DataArray of shape (y, x, band), chunked by Dask.

    Returns:
        Atmospherically corrected DataArray clipped to [0, 1].
    """
    dark = cube.quantile(0.01, dim=['y', 'x'])
    corrected = (cube - dark).clip(min=0, max=1)
    return corrected


def bad_band_mask(wavelength_coord: xr.DataArray) -> np.ndarray:
    """
    Builds a boolean mask identifying usable bands — i.e., bands outside
    the three main water absorption windows:
        ~1350–1500 nm  (first water vapour window)
        ~1800–2000 nm  (second water vapour window)
        >2400 nm       (CO2 absorption tail)

    Args:
        wavelength_coord: The 'wavelength' coordinate from an xr.DataArray.

    Returns:
        Boolean numpy array — True means the band is usable.
    """
    wl = wavelength_coord.values
    mask = (
        (wl < 1300) |
        ((wl > 1500) & (wl < 1800)) |
        ((wl > 2000) & (wl < 2400))
    )
    return mask


def savgol_lazy(cube: xr.DataArray, window: int = 7, polyorder: int = 2) -> xr.DataArray:
    """
    Savitzky-Golay spectral smoothing — applied across all bands at once.

    Uses xr.apply_ufunc with dask='parallelized' so each Dask chunk is
    processed independently and in parallel. Replaces the previous O(N_pixels)
    Python loop with a single vectorized scipy call per chunk.

    Args:
        cube:      xr.DataArray of shape (y, x, band), chunked by Dask.
        window:    Savitzky-Golay window length (must be odd). Default 7.
        polyorder: Polynomial order. Default 2.

    Returns:
        Smoothed DataArray of the same shape and dtype.
    """
    from scipy.signal import savgol_filter

    return xr.apply_ufunc(
        savgol_filter,
        cube,
        kwargs={
            'window_length': window,
            'polyorder': polyorder,
            'axis': -1,       # Apply along the band dimension
            'mode': 'nearest'
        },
        dask='parallelized',
        output_dtypes=[cube.dtype]
    )


# ── Legacy helpers — kept for backwards compatibility ──────────────────────────
# These are no longer used in main.py but preserved so any teammate code that
# imports them directly does not break immediately.

def quac_correction(cube: np.ndarray) -> np.ndarray:
    """Legacy numpy version. Use quac_correction_lazy() for Dask pipelines."""
    dark = np.percentile(cube, 1, axis=(0, 1))
    corrected = cube - dark
    return np.clip(corrected, 0, 1)


def get_good_bands_indices(wavelengths):
    """Legacy list-based version. Use bad_band_mask() for Xarray pipelines."""
    good = []
    for i, wl in enumerate(wavelengths):
        if (wl < 1300) or (1500 < wl < 1800) or (2000 < wl < 2400):
            good.append(i)
    return good, [wavelengths[i] for i in good]


def apply_bad_bands_filter(cube: np.ndarray, indices) -> np.ndarray:
    """Legacy numpy version. Use bad_band_mask() for Xarray pipelines."""
    return cube[:, :, indices]


def apply_savitzky_golay(cube: np.ndarray, window: int = 7, polyorder: int = 2) -> np.ndarray:
    """
    Legacy numpy version — vectorized fix applied.

    Previously used a pixel-by-pixel loop (O(N_pixels) iterations).
    Now uses scipy's native axis parameter for a single vectorized call.
    Use savgol_lazy() for Dask pipelines.
    """
    from scipy.signal import savgol_filter
    return savgol_filter(cube, window_length=window, polyorder=polyorder,
                         axis=-1, mode='nearest')


def save_bad_bands(wavelengths):
    """Legacy stub — no longer used in the pipeline."""
    pass
