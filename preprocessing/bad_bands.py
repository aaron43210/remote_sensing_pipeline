# =============================================================
# OWNER: ANANTAHANARAYANAN
# =============================================================
"""
Decide which bands are unusable. See interpolation.py for what replaces them.

Two independent sources of bad bands:
    1. Atmospheric water-vapour cores and detector edges -- known from the
       wavelength axis alone, before we ever look at the data.
    2. Low per-band signal-to-noise -- measured from the data itself.

These bands are flagged, never deleted; interpolation.py explains why.
"""

import logging
import numpy as np

import config

logger = logging.getLogger(__name__)


def build_mask(wavelengths):
    """Flag bands unusable from the wavelength axis alone. True = BAD."""
    wl = np.asarray(wavelengths, dtype=float)
    mask = np.zeros(wl.shape, dtype=bool)

    for low, high in config.WATER_WINDOWS:
        mask |= (wl >= low) & (wl <= high)

    # Spectrometer edges: low signal, high noise.
    mask |= (wl < config.WAVELENGTH_MIN) | (wl > config.WAVELENGTH_MAX)

    logger.info("Bad-band mask: %d of %d bands flagged by wavelength",
                int(mask.sum()), mask.size)
    return mask


def _estimate_noise(flat):
    """
    Per-band noise, estimated from band-to-band differences.

    Adjacent bands of a real spectrum are highly correlated, so most of what
    survives a spectral difference is sensor noise. Dividing by sqrt(2)
    undoes the variance doubling of differencing two noisy bands.

    We deliberately do NOT use the spatial standard deviation: in a scene
    containing both a field and a rooftop that variability is real signal,
    and treating it as noise would flag the most informative bands.
    """
    diff = np.diff(flat, axis=1)
    noise = np.empty(flat.shape[1], dtype=np.float64)
    noise[1:] = diff.std(axis=0) / np.sqrt(2.0)
    noise[0] = noise[1]                  # first band has no left neighbour
    return noise


def snr_reject(cube, mask):
    """
    Add bands with too little signal-to-noise to the mask.

    A band that is mostly noise tells us nothing, and smoothing would smear
    that noise into its neighbours.
    """
    flat = cube.reshape(-1, cube.shape[-1])
    if flat.shape[1] < 2:
        return mask

    signal = np.abs(flat.mean(axis=0))
    noise = _estimate_noise(flat)

    # Dead (constant) band -> SNR 0 so it gets flagged, not inf so it survives.
    with np.errstate(divide="ignore", invalid="ignore"):
        snr = np.where(noise > 1e-10, signal / noise, 0.0)

    combined = mask | (snr < config.MIN_BAND_SNR)
    added = int(combined.sum() - mask.sum())
    if added:
        logger.info("SNR rejection flagged %d additional bands", added)
    return combined
