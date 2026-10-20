"""
The "normal method" for thermal: services/thermal/anomaly_detector.py rules.

    global anomaly   z = (T - mean) / std over valid pixels; hot z > 2, cold z < -2
    local anomaly    11x11 neighbourhood z > 2
    UHI zones        urban = T > p80, rural = T < p20 (no land-cover map)
    hotspots         T > p95, connected regions of >= 10 pixels

Valid pixel = finite and != 0 (the detector's own convention: Landsat fill is 0).
One deliberate, documented difference: before the 11x11 filter, invalid pixels
are filled with the scene mean instead of being left as 0/NaN, so a cloud hole
does not drag every neighbour's local mean towards 0 C. Teacher and network
both use this, so their maps stay comparable.
"""

import numpy as np
from scipy import ndimage

from .. import spec


def valid_mask(lst):
    lst = np.asarray(lst, dtype=float)
    return np.isfinite(lst) & (lst != 0)


def scene_stats(lst, valid=None):
    valid = valid_mask(lst) if valid is None else valid
    v = np.asarray(lst, dtype=float)[valid]
    if v.size < 10:
        raise ValueError("fewer than 10 valid thermal pixels")
    return {"mean": float(v.mean()), "std": float(max(v.std(), 0.01)),
            "flat": bool(v.std() < 0.01),          # detector: no global anomalies
            "p20": float(np.percentile(v, spec.THERMAL_RURAL_PERCENTILE)),
            "p80": float(np.percentile(v, spec.THERMAL_URBAN_PERCENTILE)),
            "p95": float(np.percentile(v, spec.THERMAL_HOT_PERCENTILE)),
            "n_valid": int(v.size)}


def filled(lst, valid, stats):
    return np.where(valid, lst, stats["mean"]).astype(float)


def local_z(lst_filled, window=spec.THERMAL_LOCAL_WINDOW):
    m = ndimage.uniform_filter(lst_filled, size=window)
    sq = ndimage.uniform_filter(lst_filled ** 2, size=window)
    s = np.sqrt(np.maximum(sq - m ** 2, 0.0))
    return np.where(s > 0.01, (lst_filled - m) / np.where(s > 0.01, s, 1.0), 0.0)


def hotspot_regions(hot_mask, min_px=spec.THERMAL_MIN_HOTSPOT_PX):
    """Connected regions of a boolean mask, regions smaller than min_px removed."""
    labels, n = ndimage.label(hot_mask)
    if n == 0:
        return labels.astype(np.int32)
    sizes = ndimage.sum(np.ones_like(labels), labels, index=np.arange(1, n + 1))
    small = np.flatnonzero(sizes < min_px) + 1
    labels[np.isin(labels, small)] = 0
    return labels.astype(np.int32)


def teacher_maps(lst):
    """
    lst: (rows, cols) Celsius, invalid = NaN or 0.
    Returns dict of maps; categorical maps use 255 for invalid pixels.
    """
    lst = np.asarray(lst, dtype=float)
    valid = valid_mask(lst)
    st = scene_stats(lst, valid)
    lf = filled(lst, valid, st)
    z = np.zeros_like(lf) if st["flat"] else (lf - st["mean"]) / st["std"]
    lz = local_z(lf)

    glob = np.full(lst.shape, 1, dtype=np.uint8)
    glob[z < -spec.THERMAL_Z] = 0
    glob[z > spec.THERMAL_Z] = 2
    uhi = np.full(lst.shape, 1, dtype=np.uint8)
    uhi[lf < st["p20"]] = 0
    uhi[lf > st["p80"]] = 2
    hot = lf > st["p95"]
    local = lz > spec.THERMAL_Z
    for m in (glob, uhi):
        m[~valid] = 255
    hot &= valid
    local &= valid
    regions = hotspot_regions(hot)
    urban, rural = lf[valid & (uhi == 2)], lf[valid & (uhi == 0)]
    uhi_intensity = float(urban.mean() - rural.mean()) if urban.size and rural.size else 0.0
    return {"global_class": glob, "uhi_class": uhi, "hot_p95": hot, "local_anomaly": local,
            "hotspots": regions, "z": np.where(valid, z, np.nan), "local_z": np.where(valid, lz, np.nan),
            "uhi_intensity_c": uhi_intensity, "stats": st, "valid": valid}
