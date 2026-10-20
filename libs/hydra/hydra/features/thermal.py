"""LST raster -> per-pixel thermal input vectors (spec.THERMAL_FEATURES)."""

import numpy as np
from scipy import ndimage

from .. import spec
from ..classical import thermal as rules


def raster_features(lst):
    """
    lst: (rows, cols) Celsius, invalid = NaN or 0.
    Returns (features (rows*cols, 10) float32, valid (rows, cols), stats).
    Everything is scene-relative (divided by the scene std), so one network
    serves a winter scene at 2 C and a summer scene at 45 C.
    """
    lst = np.asarray(lst, dtype=float)
    valid = rules.valid_mask(lst)
    st = rules.scene_stats(lst, valid)
    lf = rules.filled(lst, valid, st)
    s = st["std"]
    m3 = ndimage.uniform_filter(lf, size=3)
    sq3 = ndimage.uniform_filter(lf ** 2, size=3)
    std3 = np.sqrt(np.maximum(sq3 - m3 ** 2, 0.0))
    rng3 = ndimage.maximum_filter(lf, size=3) - ndimage.minimum_filter(lf, size=3)
    f = np.stack([
        np.zeros_like(lf) if st["flat"] else (lf - st["mean"]) / s,
        (lf - st["p95"]) / s,
        (lf - st["p80"]) / s,
        (lf - st["p20"]) / s,
        rules.local_z(lf),
        (lf - m3) / s,
        std3 / s,
        rng3 / s,
        (lf - 20.0) / 20.0,
        np.full(lf.shape, np.log10(s)),
    ], axis=-1)
    return f.reshape(-1, len(spec.THERMAL_FEATURES)).astype(np.float32), valid, st
