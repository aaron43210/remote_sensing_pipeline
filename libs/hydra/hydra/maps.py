"""
Scene -> maps, for HydraNet AND for the classical method, through one code path.

The inference service calls the `hydra_*` functions; evaluation and shadow
verification call the `classical_*` ones on the same inputs and compare. Masks
(nodata / water / vegetation / bare) are computed once and applied to both.

Categorical maps use 255 for "not mapped here".
"""

import numpy as np

from . import spec
from .classical import thermal as thermal_rules
from .classical.agri_lut import LUTInverter
from .classical.mineral import MineralTeacher
from .features import agri as agri_features
from .features.masks import pixel_classes
from .features.mineral import MineralFeatures, to_good_bands
from .features.thermal import raster_features

NOT_MAPPED = 255
_FEATURES = None


def mineral_features():
    global _FEATURES
    if _FEATURES is None:
        _FEATURES = MineralFeatures()
    return _FEATURES


def scene_masks(cube, wavelengths, cloud=None):
    return pixel_classes(cube, wavelengths, cloud)


# ── Mineral ──────────────────────────────────────────────────────────────────

def mineral_inputs(cube, wavelengths, mask):
    """Features for the pixels where mask is True. Returns (x, reflectance, flat_index)."""
    flat = np.flatnonzero(mask.ravel())
    refl = to_good_bands(cube.reshape(-1, cube.shape[-1])[flat], wavelengths)
    x = mineral_features().transform(refl)
    ok = np.isfinite(x).all(axis=1)
    return x[ok], refl[ok], flat[ok]


def _mineral_layers(shape, flat, cls, conf, abund, quality):
    n = shape[0] * shape[1]
    out_cls = np.full(n, NOT_MAPPED, np.uint8)
    out_conf = np.full(n, np.nan, np.float32)
    out_q = np.full(n, np.nan, np.float32)
    out_ab = np.full((n, len(spec.MINERALS)), np.nan, np.float32)
    out_cls[flat], out_conf[flat], out_q[flat], out_ab[flat] = cls, conf, quality, abund
    return {"mineral_class": out_cls.reshape(shape), "mineral_confidence": out_conf.reshape(shape),
            "mineral_quality": out_q.reshape(shape),
            "mineral_abundance": out_ab.reshape(*shape, len(spec.MINERALS))}


def hydra_mineral(runtime, cube, wavelengths, mask):
    x, _, flat = mineral_inputs(cube, wavelengths, mask)
    if flat.size == 0:
        return _mineral_layers(cube.shape[:2], flat, [], [], np.empty((0, 12)), [])
    p = runtime.predict("mineral", x)
    probs = p["mineral_class_probs"]
    return _mineral_layers(cube.shape[:2], flat, probs.argmax(1).astype(np.uint8), probs.max(1),
                           p["mineral_abundance"], p["mineral_quality"])


def classical_mineral(cube, wavelengths, mask):
    x, refl, flat = mineral_inputs(cube, wavelengths, mask)
    lab = MineralTeacher(mineral_features()).label(refl, x)
    return _mineral_layers(cube.shape[:2], flat, lab["class_index"].astype(np.uint8),
                           np.full(flat.size, np.nan), lab["abundances"], lab["quality"])


# ── Agriculture ──────────────────────────────────────────────────────────────

def agri_inputs(cube, wavelengths, mask):
    flat = np.flatnonzero(mask.ravel())
    x = agri_features.select(cube.reshape(-1, cube.shape[-1])[flat], wavelengths)
    ok = np.isfinite(x).all(axis=1)
    return x[ok], flat[ok]


def _agri_layers(shape, flat, params, std):
    n = shape[0] * shape[1]
    out = {}
    for j, name in enumerate(spec.AGRI_PARAMS):
        v = np.full(n, np.nan, np.float32)
        s = np.full(n, np.nan, np.float32)
        v[flat], s[flat] = params[:, j], std[:, j]
        out[f"agri_{name}"] = v.reshape(shape)
        out[f"agri_{name}_std"] = s.reshape(shape)
    return out


def hydra_agri(runtime, cube, wavelengths, mask):
    x, flat = agri_inputs(cube, wavelengths, mask)
    if flat.size == 0:
        return _agri_layers(cube.shape[:2], flat, np.empty((0, 6)), np.empty((0, 6)))
    p = runtime.predict("agri", x)
    return _agri_layers(cube.shape[:2], flat, p["agri_params"], p["agri_std"])


def classical_agri(lut: LUTInverter, cube, wavelengths, mask):
    x, flat = agri_inputs(cube, wavelengths, mask)
    r = lut.invert(x)
    return _agri_layers(cube.shape[:2], flat, r["params"], r["std"])


# ── Thermal ──────────────────────────────────────────────────────────────────

def hydra_thermal(runtime, lst):
    f, valid, st = raster_features(lst)
    shape = valid.shape
    flat = np.flatnonzero(valid.ravel())
    p = runtime.predict("thermal", f[flat])
    n = valid.size
    glob = np.full(n, NOT_MAPPED, np.uint8)
    uhi = np.full(n, NOT_MAPPED, np.uint8)
    hot = np.zeros(n, bool)
    local = np.zeros(n, bool)
    glob[flat] = p["thermal_global_probs"].argmax(1)
    uhi[flat] = p["thermal_uhi_probs"].argmax(1)
    hot[flat] = p["thermal_hot_prob"] > 0.5
    local[flat] = p["thermal_local_prob"] > 0.5
    lf = thermal_rules.filled(np.asarray(lst, float), valid, st)
    uhi = uhi.reshape(shape)
    urban, rural = lf[valid & (uhi == 2)], lf[valid & (uhi == 0)]
    return {"thermal_global_class": glob.reshape(shape), "thermal_uhi_class": uhi,
            "thermal_hotspots": thermal_rules.hotspot_regions(hot.reshape(shape)),
            "thermal_local_anomaly": local.reshape(shape).astype(np.uint8),
            "thermal_lst": np.where(valid, lst, np.nan).astype(np.float32),
            "uhi_intensity_c": float(urban.mean() - rural.mean()) if urban.size and rural.size else 0.0,
            "stats": st}


def classical_thermal(lst):
    t = thermal_rules.teacher_maps(lst)
    return {"thermal_global_class": t["global_class"], "thermal_uhi_class": t["uhi_class"],
            "thermal_hotspots": t["hotspots"],
            "thermal_local_anomaly": t["local_anomaly"].astype(np.uint8),
            "thermal_lst": np.where(t["valid"], lst, np.nan).astype(np.float32),
            "uhi_intensity_c": t["uhi_intensity_c"], "stats": t["stats"]}


# ── Agreement ────────────────────────────────────────────────────────────────

def value_range(layer):
    """Physical range of a continuous layer, for range-normalised error (nMAE); None if open-ended."""
    if layer.startswith("agri_") and not layer.endswith("_std"):
        lo, hi = spec.AGRI_RANGES[layer[5:]]
        return hi - lo
    if layer in ("mineral_abundance", "mineral_quality"):
        return 1.0
    return None


def agreement(nn, ref):
    """
    Per-layer agreement between two map dicts (only where both are mapped).
    Continuous layers report MAE, nMAE (MAE / physical range: the judged metric,
    stable when an area's values barely vary) and R² (informative only: it
    collapses towards -inf when the reference has almost no variance).
    """
    out = {}
    for k, a in nn.items():
        b = ref.get(k)
        if not isinstance(a, np.ndarray) or b is None:
            continue
        if a.dtype == np.uint8 or k == "thermal_hotspots":
            if k == "thermal_hotspots":
                a, b = a > 0, b > 0
                m = np.ones(a.shape, bool)
            else:
                m = (a != NOT_MAPPED) & (b != NOT_MAPPED)
            if m.any():
                out[k] = {"pixel_agreement": float((a[m] == b[m]).mean()), "n": int(m.sum())}
        else:
            m = np.isfinite(a) & np.isfinite(b)
            if m.any():
                d = a[m] - b[m]
                ss = ((b[m] - b[m].mean()) ** 2).sum()
                out[k] = {"mae": float(np.abs(d).mean()),
                          "r2": float(1 - (d ** 2).sum() / ss) if ss > 0 else None,
                          "n": int(m.sum())}
                rng = value_range(k)
                if rng:
                    out[k]["nmae"] = out[k]["mae"] / rng
    return out
