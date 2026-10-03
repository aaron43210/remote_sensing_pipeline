# =============================================================
# OWNER: ANANTAHANARAYANAN
# =============================================================
"""
Convert Landsat Level-2 surface reflectance bands from DN to reflectance.

    reflectance = DN * 0.0000275 - 0.2

The optical counterpart of convert.py, which does the thermal bands. Same
shape: pure functions, no I/O, so the maths can be unit tested offline.

WHY THE THERMAL BRANCH NEEDS OPTICAL BANDS
    Land surface temperature depends on emissivity, and emissivity is
    normally estimated from NDVI -- which needs the red and near-infrared
    bands. So the thermal service asks for reflectance bands even though its
    own product is temperature.
"""

import logging
import numpy as np

logger = logging.getLogger(__name__)

# Physically valid surface reflectance. Values outside this are retrieval
# failures -- cloud edge, deep shadow, saturation -- not measurements.
REFLECTANCE_MIN, REFLECTANCE_MAX = 0.0, 1.0


def dn_to_reflectance(dn, scale, offset):
    """Apply the scene's radiometric scaling. Returns float32."""
    return (np.asarray(dn, dtype=np.float32) * scale + offset).astype(
        np.float32)


def mask_implausible(reflectance):
    """
    Set physically impossible reflectance to NaN.

    Collection 2 uses 0 as fill, which maps to -0.2 after scaling, so fill
    falls outside the valid range and is caught here automatically.
    """
    out = np.array(reflectance, dtype=np.float32, copy=True)
    out[(out < REFLECTANCE_MIN) | (out > REFLECTANCE_MAX)] = np.nan
    return out


def statistics(reflectance):
    """Summarise the valid pixels. All zero when nothing is valid."""
    valid = reflectance[~np.isnan(reflectance)]

    if valid.size == 0:
        logger.warning("No valid reflectance -- all pixels masked")
        return {"min": 0.0, "max": 0.0, "mean": 0.0, "std": 0.0}

    return {
        "min": float(np.min(valid)),
        "max": float(np.max(valid)),
        "mean": float(np.mean(valid)),
        "std": float(np.std(valid)),
    }


def prepare(dn, scale, offset, invalid=None, fill=0.0):
    """
    Full conversion: DN -> reflectance -> masked -> statistics -> filled.

    `invalid` is an optional boolean mask of extra pixels to discard, e.g.
    the cloud mask from qa.cloud_mask().

    Statistics come from VALID pixels only, and fill is applied LAST -- the
    same ordering the thermal path uses, and for the same reason: 0.0 is a
    plausible reflectance, so letting fill into the mean corrupts it.

    Returns (reflectance_filled, stats_dict).
    """
    values = mask_implausible(dn_to_reflectance(dn, scale, offset))
    if invalid is not None:
        values[invalid] = np.nan

    stats = statistics(values)
    fraction = float(np.isnan(values).mean())
    stats["invalid_fraction"] = round(fraction, 4)

    logger.info("Converted reflectance: shape=%s, [%.3f, %.3f], %.1f%% invalid",
                values.shape, stats["min"], stats["max"], fraction * 100)

    return np.nan_to_num(values, nan=fill), stats
