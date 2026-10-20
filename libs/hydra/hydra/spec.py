"""
The one specification shared by training, ONNX export and inference.

Everything that decides what a number in a feature vector or a model output
MEANS lives here: band lists, class names, physical ranges, thresholds of the
classical ("normal") methods. `feature_spec_hash()` fingerprints it; the hash
is baked into the ONNX file and checked at load, so a model can never be run
on features computed differently from the ones it was trained on.
"""

import hashlib
import json
from functools import lru_cache
from pathlib import Path

import numpy as np

DATA_DIR = Path(__file__).resolve().parent / "data"
USGS_LIBRARY_DIR = DATA_DIR / "usgs_library"

SPEC_VERSION = 1

# ── Mineral (EMIT, 255 good bands, USGS library) ────────────────────────────
MINERALS = ("alunite", "calcite", "chlorite", "dolomite", "goethite", "gypsum",
            "hematite", "illite", "kaolinite", "montmorillonite", "muscovite",
            "quartz")
UNKNOWN_CLASS = len(MINERALS)              # 12: SAM best angle above threshold
MINERAL_CLASSES = MINERALS + ("unknown",)

# Diagnostic absorption windows, identical to services/mineral_analysis/config.py
DIAGNOSTIC_FEATURES = {
    "Al-OH": (2100.0, 2300.0),
    "Fe-OH": (2200.0, 2330.0),
    "Mg-OH": (2250.0, 2400.0),
    "CO3": (2300.0, 2400.0),
    "Fe3+": (750.0, 1050.0),
    "Fe2+": (900.0, 1200.0),
    "Sulfate": (1700.0, 2300.0),
}

# Classical method settings (services/mineral_analysis/config.py defaults)
SAM_MAX_ANGLE = 0.20       # radians; larger best angle -> "unknown"
SMA_MAX_RMSE = 0.10        # reflectance units
SMA_SUM_WEIGHT = 1e3       # weight of the sum-to-one row in FCLS

N_MINERAL_FEATURES = 255 + len(DIAGNOSTIC_FEATURES) + len(MINERALS) + 1   # 275

# ── Agriculture (PROSAIL) ───────────────────────────────────────────────────
# The 24 wavelengths of RTM_NN_TRAINING_DATASET_WAVELENGTHS.csv minus 1357 and
# 1417 nm, which sit on the water-vapour edges and are noise on real sensors.
AGRI_DATASET_BANDS_NM = (403, 425, 440, 462, 485, 500, 522, 544, 567, 589, 604,
                         701, 738, 895, 977, 992, 1051, 1074, 1096, 1141, 1357,
                         1417, 1536, 1558)
AGRI_BANDS_NM = tuple(b for b in AGRI_DATASET_BANDS_NM if b not in (1357, 1417))
AGRI_MAX_BAND_GAP_NM = 4.0
AGRI_PARAMS = ("Cab", "Car", "Cw", "Cm", "LAI", "ALA")
AGRI_UNITS = ("ug/cm2", "ug/cm2", "g/cm2", "g/cm2", "m2/m2", "deg")
# Uniform sampling ranges of the training CSV (min/max observed, rounded out)
AGRI_RANGES = {"Cab": (0.0, 110.0), "Car": (0.0, 30.0), "Cw": (0.0, 0.07),
               "Cm": (0.0, 0.05), "LAI": (0.0, 10.0), "ALA": (0.0, 90.0)}
# PROSAIL settings that reproduce the CSV (fitted, RMSE 7e-5 reflectance)
PROSAIL_FIXED = {"n": 1.5, "cbrown": 0.0, "hspot": 0.1, "tts": 30.0, "tto": 0.0,
                 "psi": 0.0, "psoil": 0.5, "rsoil": 1.0, "typelidf": 2,
                 "factor": "SDR", "prospect_version": "5"}
LUT_K = 20                 # LUT inversion: inverse-distance mean of the 20 nearest (k=5 was unstable: see training/README)
VEG_NDVI_MIN = 0.25        # agriculture is mapped on vegetated pixels only

# ── Thermal (Landsat LST, ThermalAnomalyDetector rules) ─────────────────────
THERMAL_Z = 2.0            # global and local z thresholds
THERMAL_LOCAL_WINDOW = 11  # local z window (pixels)
THERMAL_HOT_PERCENTILE = 95.0
THERMAL_MIN_HOTSPOT_PX = 10
THERMAL_URBAN_PERCENTILE = 80.0
THERMAL_RURAL_PERCENTILE = 20.0
THERMAL_FEATURES = ("z_global", "d_p95", "d_p80", "d_p20", "z_local11",
                    "d_local3_mean", "local3_std", "local3_range",
                    "lst_norm", "scene_std")
THERMAL_GLOBAL_CLASSES = ("cold", "normal", "hot")
THERMAL_UHI_CLASSES = ("rural", "neutral", "urban")


@lru_cache(maxsize=1)
def emit_bands():
    """(wavelengths_nm (285,), good_mask (285,)) of the EMIT sensor."""
    d = json.loads((DATA_DIR / "emit_bands.json").read_text())
    wl = np.array([b["wavelength_nm"] for b in d["bands"]], dtype=float)
    good = np.array([b["good"] for b in d["bands"]], dtype=bool)
    return wl, good


def emit_good_wavelengths():
    wl, good = emit_bands()
    return wl[good]


def mineral_feature_names():
    names = [f"cr_{w:.1f}nm" for w in emit_good_wavelengths()]
    names += [f"depth_{k}" for k in DIAGNOSTIC_FEATURES]
    names += [f"sam_{m}" for m in MINERALS]
    return names + ["brightness"]


def as_dict():
    return {
        "spec_version": SPEC_VERSION,
        "minerals": list(MINERALS),
        "mineral_classes": list(MINERAL_CLASSES),
        "emit_good_wavelengths": [round(float(w), 4) for w in emit_good_wavelengths()],
        "diagnostic_features": {k: list(v) for k, v in DIAGNOSTIC_FEATURES.items()},
        "sam_max_angle": SAM_MAX_ANGLE, "sma_max_rmse": SMA_MAX_RMSE,
        "sma_sum_weight": SMA_SUM_WEIGHT,
        "agri_bands_nm": list(AGRI_BANDS_NM), "agri_params": list(AGRI_PARAMS),
        "agri_units": list(AGRI_UNITS),
        "agri_ranges": {k: list(v) for k, v in AGRI_RANGES.items()},
        "prosail_fixed": PROSAIL_FIXED, "lut_k": LUT_K, "veg_ndvi_min": VEG_NDVI_MIN,
        "thermal": {"z": THERMAL_Z, "local_window": THERMAL_LOCAL_WINDOW,
                    "hot_percentile": THERMAL_HOT_PERCENTILE,
                    "min_hotspot_px": THERMAL_MIN_HOTSPOT_PX,
                    "urban_percentile": THERMAL_URBAN_PERCENTILE,
                    "rural_percentile": THERMAL_RURAL_PERCENTILE,
                    "features": list(THERMAL_FEATURES),
                    "global_classes": list(THERMAL_GLOBAL_CLASSES),
                    "uhi_classes": list(THERMAL_UHI_CLASSES)},
        "library_sha256": library_sha256(),
    }


@lru_cache(maxsize=1)
def library_sha256():
    h = hashlib.sha256()
    for name in MINERALS:
        data = np.loadtxt(USGS_LIBRARY_DIR / f"{name}.csv", delimiter=",",
                          skiprows=3)
        h.update(np.round(data, 6).tobytes())
    return h.hexdigest()


def feature_spec_hash():
    blob = json.dumps(as_dict(), sort_keys=True).encode()
    return hashlib.sha256(blob).hexdigest()[:16]
