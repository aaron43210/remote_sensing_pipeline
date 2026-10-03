# =============================================================
# OWNER: ANANTAHANARAYANAN
# =============================================================
"""
Decode the Landsat QA_PIXEL band to find cloud.

Landsat Collection 2 ships a 16-bit quality mask, one bit per condition. We
mask cloud, its dilated margin, cirrus, cloud shadow and scene fill. A cloud
top is far colder than the ground, so an unmasked cloud reads as a huge cold
anomaly -- on the Punjab scene, -15 C in a scene averaging 34 C.

Snow and water are deliberately NOT masked: both are genuine surfaces with
genuine temperatures.

Reference: USGS Landsat C2 Level-2 Science Product Guide (2021), Table 6-3.
"""

import logging
import os

import numpy as np

from . import cog

logger = logging.getLogger(__name__)

# Bit positions in QA_PIXEL, Landsat Collection 2.
FILL = 1 << 0
DILATED_CLOUD = 1 << 1
CIRRUS = 1 << 2
CLOUD = 1 << 3
CLOUD_SHADOW = 1 << 4

# What we treat as unusable for a surface temperature product.
UNUSABLE = FILL | DILATED_CLOUD | CIRRUS | CLOUD | CLOUD_SHADOW


def find_qa(thermal_path):
    """
    Locate the QA_PIXEL band beside the thermal band.

    Returns the path, or None if the scene was downloaded without it.
    """
    folder = os.path.dirname(os.path.abspath(thermal_path))
    for name in sorted(os.listdir(folder)):
        if "QA_PIXEL" in name.upper() and name.upper().endswith(".TIF"):
            return os.path.join(folder, name)
    return None


def read_quality(qa_path, bbox):
    """
    Cloud mask and breakdown for one window of QA_PIXEL.

    Used for every Landsat band (process.py), so no path can skip masking --
    an earlier automated path did. QA shares the scene's grid, so the same
    bbox gives the same window.

    Returns (mask, breakdown), or (None, {}) when the scene has no QA band.
    """
    if not qa_path:
        logger.warning("No QA_PIXEL band -- cloud masking NOT applied")
        return None, {}

    band, _transform, _crs = cog.read_window(qa_path, bbox)
    return cloud_mask(band), breakdown(band)


def cloud_mask(qa):
    """
    Boolean mask of pixels to discard: True where cloud, shadow, cirrus or
    fill. `qa` must share the grid and window of the band it masks.
    """
    qa = np.asarray(qa).astype(np.uint16)
    mask = (qa & UNUSABLE) != 0

    logger.info("QA mask: %.1f%% unusable (cloud, shadow, cirrus, fill)",
                100.0 * mask.mean())
    return mask


def breakdown(qa):
    """
    Per-condition percentages, for the manifest and for sanity-checking.

    Worth comparing against CLOUD_COVER in the MTL: if they disagree wildly,
    the bit decoding is wrong.
    """
    qa = np.asarray(qa).astype(np.uint16)
    size = qa.size or 1

    return {
        "fill_pct": round(100.0 * ((qa & FILL) != 0).sum() / size, 2),
        "cloud_pct": round(100.0 * ((qa & CLOUD) != 0).sum() / size, 2),
        "dilated_cloud_pct": round(100.0 * ((qa & DILATED_CLOUD) != 0).sum() / size, 2),
        "cirrus_pct": round(100.0 * ((qa & CIRRUS) != 0).sum() / size, 2),
        "cloud_shadow_pct": round(100.0 * ((qa & CLOUD_SHADOW) != 0).sum() / size, 2),
    }
