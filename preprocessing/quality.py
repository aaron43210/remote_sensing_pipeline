# =============================================================
# OWNER: ANANTAHANARAYANAN
# =============================================================
"""
Scene quality gate: refuse only a scene that is mostly empty.

It must look at the RAW values. Once normalise() has turned fill and NaN
into 0, an empty pixel looks like a black one and the gate can no longer
see it -- which is how an earlier version passed every scene.

POLICY: report everything, reject almost nothing. A partly cloudy box is
still worth processing; the user chose it.
"""

import logging

import numpy as np

import config

logger = logging.getLogger(__name__)


def assess(sample):
    """
    Args:
        sample: raw (rows, cols, bands) values, before normalise()

    Returns:
        (usable, report) -- report is JSON-safe and rides on the message.
    """
    empty = ~np.isfinite(sample) | (sample == config.FILL_VALUE)
    nodata = empty.all(axis=-1)                  # no band has a value
    fraction = float(nodata.mean()) if nodata.size else 1.0

    report = {
        "nodata_fraction": round(fraction, 4),
        # Some bands empty but not all: worth knowing, not worth rejecting.
        "partial_fraction": round(float((empty.any(axis=-1) & ~nodata).mean()
                                        if nodata.size else 0.0), 4),
    }

    usable = fraction <= config.MAX_NODATA_FRACTION
    if not usable:
        logger.error("Scene rejected: %.1f%% of pixels have no data "
                     "(limit %.0f%%)", fraction * 100,
                     config.MAX_NODATA_FRACTION * 100)
    return usable, report
