# =============================================================
# OWNER: ANANTAHANARAYANAN
# =============================================================
"""
Spectral smoothing (Savitzky-Golay).

Removes detector noise along the spectral axis while preserving the shape
and depth of absorption features -- which is exactly why we use SG here and
not a moving average. A moving average would flatten the 2205 nm Al-OH and
2350 nm carbonate features -- two of the network's 10 input bands.

PERFORMANCE NOTE (this is the core of the "lightweight" claim):
    The previous implementation looped over every pixel in Python and called
    savgol_filter once per spectrum -- 65,536 SciPy calls for one 256x256
    tile. savgol_filter is already vectorised: given axis=-1 it filters every
    spectrum in the cube in a single call. Same maths, same result, one call.

Reference:
    Savitzky, A. & Golay, M.J.E. (1964). Smoothing and differentiation of
    data by simplified least squares procedures. Analytical Chemistry 36(8).
"""

import logging
import numpy as np
from scipy.signal import savgol_filter

import config

logger = logging.getLogger(__name__)


def _valid_window(window, polyorder, n_bands):
    """
    Coerce the window length into something savgol_filter will accept.

    savgol_filter requires: window is odd, window > polyorder, window <= n_bands.
    A short-band cube (or a badly set env var) would otherwise raise deep
    inside SciPy with a confusing message, so we clamp it here and say why.
    """
    if window > n_bands:
        window = n_bands
        logger.warning("SG window > band count, clamped to %d", window)

    if window % 2 == 0:          # savgol requires an odd window
        window -= 1

    if window <= polyorder:
        return None              # caller skips smoothing entirely

    return window


def savgol(cube, window=None, polyorder=None):
    """
    Smooth every spectrum in a cube along the spectral axis.

    Args:
        cube: (rows, cols, bands) float array
        window: odd filter length; defaults to config.SG_WINDOW
        polyorder: polynomial order; defaults to config.SG_POLYORDER

    Returns:
        (rows, cols, bands) float32 array, same shape as the input.
    """
    window = config.SG_WINDOW if window is None else window
    polyorder = config.SG_POLYORDER if polyorder is None else polyorder

    n_bands = cube.shape[-1]
    window = _valid_window(window, polyorder, n_bands)

    if window is None:
        # Too few bands to smooth meaningfully. Returning the cube untouched
        # is safer than raising: the scene is still usable downstream.
        logger.warning(
            "Skipping SG smoothing: %d bands is too few for polyorder %d",
            n_bands, polyorder
        )
        return cube.astype(np.float32, copy=False)

    # The whole optimisation: one call, filtering along the spectral axis.
    smoothed = savgol_filter(
        cube,
        window_length=window,
        polyorder=polyorder,
        axis=-1,
        mode="nearest",
    )

    return smoothed.astype(np.float32, copy=False)
