# =============================================================
# OWNER: ANANTAHANARAYANAN
# =============================================================
"""
Pull the right radiometric calibration out of parsed MTL metadata.

Separate from mtl.py because reading the file and knowing which numbers mean
what are different jobs -- and the second one is where the mistakes live.

Each accessor names the GROUP it reads from. That is the whole point: the
Level-1 and Level-2 sections of an MTL use identical key names for different
values, so asking without a group silently risks the wrong one.

Landsat Collection 2 documented defaults, used when the MTL is missing:
    surface temperature   DN * 0.00341802 + 149.0   -> Kelvin
    surface reflectance   DN * 0.0000275  - 0.2     -> 0..1 reflectance
"""

import logging

from . import mtl

logger = logging.getLogger(__name__)

SR_GROUP = "LEVEL2_SURFACE_REFLECTANCE_PARAMETERS"
ST_GROUP = "LEVEL2_SURFACE_TEMPERATURE_PARAMETERS"

SR_SCALE, SR_OFFSET = 0.0000275, -0.2


def thermal_scaling(meta, default_scale, default_offset):
    """
    Scale and offset for the ST_B10 surface temperature band.

    Returns:
        (scale, offset, from_mtl) -- from_mtl says whether the scene's own
        values were found, so the caller can report it rather than guess.
    """
    scale = mtl.get(meta, "TEMPERATURE_MULT_BAND_ST_B10", ST_GROUP)
    offset = mtl.get(meta, "TEMPERATURE_ADD_BAND_ST_B10", ST_GROUP)

    if scale is None or offset is None:
        logger.info("No L2 temperature scaling in MTL -- using defaults")
        return default_scale, default_offset, False

    return float(scale), float(offset), True


def reflectance_scaling(meta, band):
    """
    Scale and offset for one Level-2 surface reflectance band.

    Args:
        band: band number, e.g. 5 for SR_B5

    Returns:
        (scale, offset, from_mtl)
    """
    scale = mtl.get(meta, f"REFLECTANCE_MULT_BAND_{band}", SR_GROUP)
    offset = mtl.get(meta, f"REFLECTANCE_ADD_BAND_{band}", SR_GROUP)

    if scale is None or offset is None:
        logger.info("No L2 reflectance scaling for band %s -- using defaults",
                    band)
        return SR_SCALE, SR_OFFSET, False

    return float(scale), float(offset), True


def band_number(path):
    """
    Work out which band a Landsat filename refers to, e.g. SR_B5 -> 5.

    Returns None when the name does not look like a numbered SR band.
    """
    stem = path.upper()
    marker = "SR_B"

    if marker not in stem:
        return None

    tail = stem.split(marker, 1)[1]
    digits = ""
    for char in tail:
        if char.isdigit():
            digits += char
        else:
            break

    return int(digits) if digits else None
