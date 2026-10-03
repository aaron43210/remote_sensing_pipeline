# =============================================================
# OWNER: ANANTAHANARAYANAN
# =============================================================
"""
The 10 bands the Hydra network reads, in the ONE order it reads them.

This list is the single source of truth. Before this file, ingestion put
900 nm last and preprocessing put it fifth, so the network got its inputs
in a different order depending on which path produced them.

Override without a code change, e.g. to match the order the model was
trained on:   PREPROC_TARGET_BANDS="450,680,720,800,900,1450,2205,2265,2320,2350"
"""

import os

import numpy as np

_DEFAULT = "450,680,720,800,900,1450,2205,2265,2320,2350"
TARGET_BANDS_NM = tuple(
    float(v) for v in os.getenv("PREPROC_TARGET_BANDS", _DEFAULT).split(","))

# EMIT bands are ~7.4 nm apart. A target further than this from every band
# means the sensor does not cover it -- say so instead of picking junk.
MAX_GAP_NM = float(os.getenv("PREPROC_MAX_BAND_GAP_NM", "15"))


def column_names(targets=TARGET_BANDS_NM):
    """Stable names for the 10 features, e.g. 'b450' ... 'b2350'."""
    return [f"b{int(round(t))}" for t in targets]


def select(wavelengths, targets=TARGET_BANDS_NM):
    """
    Index of the sensor band nearest each target, in target order.

    Returns:
        (indices, actual_wavelengths) -- both length len(targets).

    Raises:
        ValueError when a target is outside the sensor's coverage.
    """
    wl = np.asarray(wavelengths, dtype=float)
    indices = [int(np.argmin(np.abs(wl - t))) for t in targets]

    for target, i in zip(targets, indices):
        if abs(wl[i] - target) > MAX_GAP_NM:
            raise ValueError(f"No band within {MAX_GAP_NM} nm of {target} nm "
                             f"(nearest is {wl[i]:.1f} nm)")

    return indices, [float(wl[i]) for i in indices]


def interpolated(indices, bad_mask, targets=TARGET_BANDS_NM):
    """Targets whose band was flagged bad, so its value is interpolated."""
    return [float(t) for t, i in zip(targets, indices) if bad_mask[i]]
