"""Map arrays -> COG + PNG in object storage + the Layer records the API serves."""

import numpy as np

from hs_common import geo, storage
from hs_common.storage import keys
from hydra import spec

CATEGORICAL = {
    "mineral_class": (list(spec.MINERAL_CLASSES), geo.mineral_palette(spec.MINERAL_CLASSES)),
    "landcover": (["no data / cloud", "water", "vegetation", "bare ground"], geo.LANDCOVER_COLORS),
    "thermal_global_class": (list(spec.THERMAL_GLOBAL_CLASSES), geo.THERMAL_GLOBAL_COLORS),
    "thermal_uhi_class": (list(spec.THERMAL_UHI_CLASSES), geo.THERMAL_UHI_COLORS),
    "thermal_hotspots": (["background", "hotspot"], geo.BINARY_COLORS),
    "thermal_local_anomaly": (["normal", "local anomaly"], geo.BINARY_COLORS),
}

# name -> (units, colormap, vmin, vmax); None = 2-98 % stretch per scene
CONTINUOUS = {
    "mineral_confidence": ("probability", "blue", 0.0, 1.0),
    "mineral_quality": ("score 0-1", "blue", 0.0, 1.0),
    "agri_Cab": ("ug/cm2", "blue", 0.0, 110.0),
    "agri_Car": ("ug/cm2", "blue", 0.0, 30.0),
    "agri_Cw": ("g/cm2", "blue", 0.0, 0.07),
    "agri_Cm": ("g/cm2", "blue", 0.0, 0.05),
    "agri_LAI": ("m2/m2", "blue", 0.0, 8.0),
    "agri_ALA": ("deg", "blue", 0.0, 90.0),
    "ndvi": ("index", "blue", 0.0, 1.0),
    "thermal_lst": ("degC", "orange", None, None),
}
HIST_BINS = 24


def _stats(a, lo=None, hi=None):
    v = a[np.isfinite(a)]
    if not v.size:
        return {"valid_fraction": 0.0}
    out = {"valid_fraction": round(float(v.size / a.size), 4), "mean": float(v.mean()),
           "min": float(v.min()), "max": float(v.max()),
           "p5": float(np.percentile(v, 5)), "p50": float(np.median(v)), "p95": float(np.percentile(v, 95))}
    lo = float(v.min()) if lo is None else lo
    hi = float(v.max()) if hi is None else hi
    if hi > lo:
        counts, edges = np.histogram(np.clip(v, lo, hi), bins=HIST_BINS, range=(lo, hi))
        out["histogram"] = {"edges": [round(float(e), 6) for e in edges], "counts": counts.tolist()}
    return out


def write(job, task, name, data, georef, units=None, colormap=None, vmin=None, vmax=None):
    """Write one layer; returns the dict for messages.Layer."""
    bounds = geo.bounds_from_transform(georef["transform"], georef["width"], georef["height"])
    tif_key = keys.result(job, task, f"{name}.tif")
    png_key = keys.result(job, task, f"{name}.png")
    if name in CATEGORICAL:
        classes, palette = CATEGORICAL[name]
        arr = np.asarray(data).astype(np.uint8)
        storage.put_bytes(tif_key, geo.cog_bytes(arr, georef["transform"], georef["crs"], nodata=255), "image/tiff")
        storage.put_bytes(png_key, geo.png_bytes(geo.colorize(arr, "categorical", palette=palette)), "image/png",
                          cache_control="private, max-age=86400")
        mapped = arr[arr != 255]
        counts = np.bincount(mapped, minlength=len(classes))[:len(classes)] if mapped.size else np.zeros(len(classes))
        frac = (counts / max(mapped.size, 1)).round(4)
        extra = {}
        if name == "mineral_class":
            extra["groups"] = {c: geo.mineral_group_of(c) for c in classes}
        return {"name": name, "tif_key": tif_key, "png_key": png_key, "bounds": bounds, "units": "class",
                "kind": "categorical", "colormap": "categorical", "classes": classes,
                "stats": {"class_fraction": dict(zip(classes, map(float, frac))), "palette": palette,
                          "class_pixels": dict(zip(classes, map(int, counts))),
                          "mapped_pixels": int(mapped.size), **extra}}
    d_units, d_cmap, d_lo, d_hi = CONTINUOUS.get(name, ("", "blue", None, None))
    units, colormap = units or d_units, colormap or d_cmap
    vmin = d_lo if vmin is None else vmin
    vmax = d_hi if vmax is None else vmax
    arr = np.asarray(data, dtype=np.float32)
    st = _stats(arr, vmin, vmax)
    if vmin is None and "p5" in st:
        vmin, vmax = st["p5"], st["p95"] if st["p95"] > st["p5"] else st["p5"] + 1e-3
    storage.put_bytes(tif_key, geo.cog_bytes(arr, georef["transform"], georef["crs"]), "image/tiff")
    storage.put_bytes(png_key, geo.png_bytes(geo.colorize(arr, "continuous", colormap, vmin, vmax)), "image/png",
                      cache_control="private, max-age=86400")
    st["ramp"] = geo.RAMPS[colormap]
    return {"name": name, "tif_key": tif_key, "png_key": png_key, "bounds": bounds, "units": units,
            "kind": "continuous", "colormap": colormap, "vmin": vmin, "vmax": vmax, "stats": st}
