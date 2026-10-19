import os
import sys
from pathlib import Path

# Ensure PROJ uses rasterio's bundled proj_data rather than system PostgreSQL PostGIS
for _p in sys.path:
    _cand = os.path.join(_p, "rasterio", "proj_data")
    if os.path.isdir(_cand):
        os.environ["PROJ_DATA"] = _cand
        os.environ["PROJ_LIB"] = _cand
        break

BASE_DIR = Path(__file__).resolve().parent

# --- Paths (each can be overridden by an environment variable) ---
_CUBE_FILE = BASE_DIR / "real_emit_multiband_cube.tif"
_DEFAULT_INPUT = _CUBE_FILE if _CUBE_FILE.is_file() else BASE_DIR / "data" / "input" / "hyperspectral.tif"
INPUT_RASTER = Path(os.getenv("MINERAL_INPUT_RASTER", _DEFAULT_INPUT))
SPECTRAL_LIBRARY_DIR = Path(os.getenv("MINERAL_LIBRARY_DIR", BASE_DIR / "data" / "usgs_library"))
OUTPUT_DIR = Path(os.getenv("MINERAL_OUTPUT_DIR", BASE_DIR / "output"))

# Output file names, written inside OUTPUT_DIR
CLASSIFICATION_FILE = "mineral_classification.tif"
ABUNDANCE_FILE = "mineral_abundance.tif"
QUALITY_FILE = "quality.tif"

# --- Thresholds: None = not set. Default thresholds enabled for quality scoring ---
SAM_MAX_ANGLE = float(os.getenv("MINERAL_SAM_MAX_ANGLE", "0.20"))  # radians; larger best angle -> "unknown"
SMA_MAX_RMSE = float(os.getenv("MINERAL_SMA_MAX_RMSE", "0.10"))    # same units as the spectra given to SMA

# --- Processing options ---
SAM_USE_CONTINUUM_REMOVAL = True  # SMA always uses the original (not continuum-removed) reflectance
SMA_NON_NEGATIVE = True           # abundance >= 0
SMA_SUM_TO_ONE = True             # abundances sum to ~1
APPLY_SCALE_OFFSET = False        # apply the raster's declared scale/offset when reading
CHUNK_PIXELS = 100_000            # pixels processed per batch (memory vs speed)
CLASS_NODATA_ID = 0               # class-map id for unknown/invalid pixels; no mineral may use it

# --- Standard Diagnostic Absorption Feature Wavelengths & Mineral Requirements (nm) ---
# References: Clark (1999), USGS Digital Spectral Library, Van der Meer et al. (2012)
DIAGNOSTIC_FEATURES = {
    "Al-OH": {
        "range": (2100.0, 2300.0),
        "center": 2205.0,
        "description": "Al-OH absorption doublet for clays, micas, and sulfates",
        "minerals": ["kaolinite", "illite", "muscovite", "alunite", "montmorillonite"],
    },
    "Fe-OH": {
        "range": (2200.0, 2330.0),
        "center": 2265.0,
        "description": "Fe-OH absorption for jarosite, nontronite, chlorite",
        "minerals": ["chlorite"],
    },
    "Mg-OH": {
        "range": (2250.0, 2400.0),
        "center": 2320.0,
        "description": "Mg-OH absorption for chlorite, serpentine, talc",
        "minerals": ["chlorite"],
    },
    "CO3": {
        "range": (2300.0, 2400.0),
        "center": 2350.0,
        "description": "Carbonate radical absorption for calcite and dolomite",
        "minerals": ["calcite", "dolomite"],
    },
    "Fe3+": {
        "range": (750.0, 1050.0),
        "center": 900.0,
        "description": "Fe3+ electronic charge transfer / crystal field for iron oxides",
        "minerals": ["hematite", "goethite"],
    },
    "Fe2+": {
        "range": (900.0, 1200.0),
        "center": 1050.0,
        "description": "Fe2+ crystal field transitions",
        "minerals": ["chlorite"],
    },
    "Sulfate": {
        "range": (1700.0, 2300.0),
        "center": 1940.0,
        "description": "Sulfate and hydration features",
        "minerals": ["gypsum", "alunite"],
    },
}

# --- Preset Diagnostic Requirement Bands ---
WAVELENGTH_PRESETS = {
    # 1. SWIR: Shortwave Infrared (2000 - 2450 nm)
    # The primary diagnostic window for alteration minerals (clays, micas, carbonates, chlorites)
    "swir": [(2000.0, 2450.0)],

    # 2. VNIR: Visible to Near-Infrared (450 - 1100 nm)
    # Primary range for iron oxides (hematite, goethite) and iron hydroxides
    "vnir": [(450.0, 1100.0)],

    # 3. Clean VNIR + SWIR (Atmospheric Water Vapor Masked)
    # Removes the strong atmospheric absorption troughs at ~1350-1420 nm and ~1800-1950 nm
    "clean": [(450.0, 1340.0), (1450.0, 1790.0), (1980.0, 2480.0)],

    # 4. Alteration Zone (Al-OH + Mg-OH + Carbonate)
    "alteration": [(2100.0, 2400.0)],

    # 5. Iron Oxide Exploration (Fe3+ band)
    "iron_oxide": [(750.0, 1050.0)],

    # 6. Carbonate Exploration (CO3 band)
    "carbonate": [(2280.0, 2420.0)],

    # 7. Full: Use all available bands
    "full": None,
}

# Default wavelength ranges (can be overridden by environment variable or CLI)
SELECTED_WAVELENGTH_RANGES = os.getenv("MINERAL_WAVELENGTH_RANGES", None)
if SELECTED_WAVELENGTH_RANGES in WAVELENGTH_PRESETS:
    SELECTED_WAVELENGTH_RANGES = WAVELENGTH_PRESETS[SELECTED_WAVELENGTH_RANGES]


def resolve_wavelength_ranges(spec):
    """Convert a preset name, feature name, string, or list of tuples into [(min, max), ...] and target minerals.

    Returns:
        (ranges, suggested_minerals)
    """
    if spec is None:
        return None, None

    if isinstance(spec, str):
        key = spec.strip()
        key_lower = key.lower()
        if key_lower in WAVELENGTH_PRESETS:
            ranges = WAVELENGTH_PRESETS[key_lower]
            minerals = None
            if key_lower in ("swir", "alteration"):
                minerals = ["kaolinite", "illite", "muscovite", "alunite", "chlorite", "calcite", "dolomite", "montmorillonite", "quartz"]
            elif key_lower in ("vnir", "iron_oxide"):
                minerals = ["hematite", "goethite", "quartz"]
            elif key_lower == "carbonate":
                minerals = ["calcite", "dolomite", "quartz"]
            return ranges, minerals

        if key in DIAGNOSTIC_FEATURES or key.upper() in DIAGNOSTIC_FEATURES:
            feat_name = key if key in DIAGNOSTIC_FEATURES else key.upper()
            feat = DIAGNOSTIC_FEATURES[feat_name]
            return [feat["range"]], feat.get("minerals")

        # Parse string like "2000-2450" or "2000 2450"
        parts = key.replace(",", " ").replace("-", " ").replace(";", " ").split()
        if len(parts) >= 2 and len(parts) % 2 == 0:
            parsed = []
            for i in range(0, len(parts), 2):
                parsed.append((float(parts[i]), float(parts[i + 1])))
            return parsed, None
        raise ValueError(f"Unknown wavelength preset or unparseable range '{spec}'. Available presets: {list(WAVELENGTH_PRESETS.keys())}")

    if isinstance(spec, (list, tuple)):
        # If it's a flat list of floats like [2000, 2450]
        if len(spec) == 2 and isinstance(spec[0], (int, float)) and isinstance(spec[1], (int, float)):
            return [(float(spec[0]), float(spec[1]))], None
        # If it's pairs like [(2000, 2450)]
        return [(float(r[0]), float(r[1])) for r in spec], None

    return None, None


def validate():
    """Raise ValueError if a numeric setting is invalid."""
    for name, value in (("SAM_MAX_ANGLE", SAM_MAX_ANGLE), ("SMA_MAX_RMSE", SMA_MAX_RMSE)):
        if value is not None and not value > 0:
            raise ValueError(f"{name} must be positive or None")
    if CHUNK_PIXELS < 1:
        raise ValueError("CHUNK_PIXELS must be >= 1")
    for r in SELECTED_WAVELENGTH_RANGES or []:
        if len(r) != 2 or not r[0] < r[1]:
            raise ValueError(f"invalid wavelength range {r}; expected (min, max) with min < max")

