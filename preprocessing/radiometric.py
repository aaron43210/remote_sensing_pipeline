# =============================================================
# OWNER: ANANTAHANARAYANAN
# =============================================================
"""
Radiometric preparation: fill values, scaling, haze removal, final clip.

    detect_scale()      once per scene: is this 0..10000 integer reflectance?
    normalise()         fill (-9999), NaN, inf -> 0; divide by the scale
    estimate_dark()     once per scene: per-band haze from the darkest pixels
    subtract_dark()     remove it -- product.py decides WHETHER to
    clip_reflectance()  clamp to [0, 1], last of all

Scale and dark are measured ONCE PER SCENE and applied to every chunk.
Measured per chunk, a dark-water chunk and a bright-field chunk would be
treated differently and the seam would show in the output.

NAMING: the earlier draft called dark subtraction "QUAC". It is not QUAC
(Bernstein et al. 2005); it is Dark Object Subtraction (Chavez 1988).
"""

import numpy as np

import config


def detect_scale(sample):
    """
    Divisor that brings this scene to 0..1 reflectance: 1 or 10000.

    Uses the 99th percentile of valid values, not the max, so one hot pixel
    cannot trigger rescaling. EMIT L2A is 0..1 already -> 1.0.
    """
    valid = sample[np.isfinite(sample) & (sample != config.FILL_VALUE)]
    if valid.size and np.percentile(valid, 99) > config.SCALED_INPUT_THRESHOLD:
        return config.REFLECTANCE_SCALE
    return 1.0


def normalise(cube, scale=1.0):
    """Fill and non-finite values -> 0, then divide by the scene's scale."""
    cube = np.asarray(cube, dtype=np.float32)
    cube = np.where(cube == config.FILL_VALUE, 0.0, cube)
    cube = np.nan_to_num(cube, nan=0.0, posinf=0.0, neginf=0.0)
    return (cube / np.float32(scale)).astype(np.float32, copy=False)


def estimate_dark(cube, percentile=None):
    """
    Per-band haze estimate: the 1st percentile of each band.

    Per band because Rayleigh scattering goes as ~1/lambda^4 -- blue carries
    far more haze than SWIR and cannot share one offset.
    """
    percentile = config.DARK_PERCENTILE if percentile is None else percentile
    return np.percentile(cube, percentile, axis=(0, 1)).astype(np.float32)


def subtract_dark(cube, dark):
    """Subtract the per-band dark vector. Does NOT clip -- see below."""
    return (cube - dark).astype(np.float32, copy=False)


def clip_reflectance(cube):
    """
    Clamp to [0, 1]. Must run LAST, after smoothing.

    Savitzky-Golay is a polynomial fit and can ring slightly below zero next
    to dark pixels; on real EMIT data 0.09% of values did. Clipping earlier
    would let those negatives through to the network.
    """
    return np.clip(cube, 0.0, 1.0).astype(np.float32, copy=False)
