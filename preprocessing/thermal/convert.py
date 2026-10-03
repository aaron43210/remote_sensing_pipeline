# =============================================================
# OWNER: ANANTAHANARAYANAN
# =============================================================
"""
The thermal preprocessing itself: raw DN -> Kelvin -> Celsius.

    Kelvin  = DN * scale + offset          scale/offset from MTL, else config
    Celsius = Kelvin - 273.15

Then implausible temperatures are masked. This is the thermal counterpart of
what radiometric.py does for the hyperspectral cube, and the functions here
are deliberately pure -- no network, no file system -- so they can be unit
tested offline.
"""

import logging
import numpy as np

from . import config

logger = logging.getLogger(__name__)


def dn_to_celsius(dn, scale, offset):
    """
    Convert raw digital numbers to degrees Celsius.

    Args:
        dn: array of raw DN values
        scale, offset: radiometric calibration, from the MTL where available

    Returns:
        float32 array of temperatures in Celsius.
    """
    kelvin = np.asarray(dn, dtype=np.float32) * scale + offset
    return (kelvin - config.KELVIN_TO_CELSIUS).astype(np.float32)


def mask_implausible(celsius):
    """
    Set physically impossible land surface temperatures to NaN.

    Values outside the bounds are sensor artefacts or fill, not measurements.
    Masking them keeps them out of the scene statistics the thermal service
    uses as its anomaly baseline -- one -9999 would hide every real hotspot.
    """
    out = np.array(celsius, dtype=np.float32, copy=True)
    out[out < config.TEMP_MIN_C] = np.nan
    out[out > config.TEMP_MAX_C] = np.nan
    return out


def statistics(celsius):
    """
    Summarise the valid (non-NaN) temperatures.

    Returns min/max/mean/std in Celsius, all zero when nothing is valid.
    """
    valid = celsius[~np.isnan(celsius)]

    if valid.size == 0:
        logger.warning("No valid temperatures in scene -- all pixels masked")
        return {"temp_min_c": 0.0, "temp_max_c": 0.0,
                "temp_mean_c": 0.0, "temp_std_c": 0.0}

    return {
        "temp_min_c": float(np.min(valid)),
        "temp_max_c": float(np.max(valid)),
        "temp_mean_c": float(np.mean(valid)),
        "temp_std_c": float(np.std(valid)),
    }


def prepare(dn, scale, offset, invalid=None):
    """
    Full conversion: DN -> Celsius -> masked -> statistics -> filled.

    `invalid` is an optional boolean mask of extra pixels to discard, e.g.
    from qa.cloud_mask(), combined with the plausibility bounds.

    Statistics come from VALID pixels only, and fill is written LAST. That
    order matters: fill is config.FILL_VALUE (0.0), and 0 C is a plausible
    temperature, so letting it reach the statistics drags the mean too cold.

    Returns (celsius_filled, stats_dict).
    """
    celsius = mask_implausible(dn_to_celsius(dn, scale, offset))
    if invalid is not None:
        celsius[invalid] = np.nan

    stats = statistics(celsius)
    fraction = float(np.isnan(celsius).mean())
    stats["invalid_fraction"] = round(fraction, 4)

    logger.info("Converted thermal: shape=%s, T=[%.1f, %.1f] C, %.1f%% invalid",
                celsius.shape, stats["temp_min_c"], stats["temp_max_c"],
                fraction * 100)

    return np.nan_to_num(celsius, nan=config.FILL_VALUE), stats
