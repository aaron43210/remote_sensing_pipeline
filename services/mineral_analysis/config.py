# =============================================================
# OWNER: ANANTHAN S & HARIKRISHNAN
# =============================================================
"""
Mineral Analysis Configuration

References:
- Clark et al. (2007): USGS Digital Spectral Library
- Van der Meer et al. (2012): Geologic remote sensing
- Kokaly et al. (2017): USGS spectral library version 7
"""

# Diagnostic absorption feature wavelengths (nm)
# Based on Hunt & Salisbury (1970), Clark (1999)
DIAGNOSTIC_FEATURES = {
    "Al-OH": {"center": 2205, "shoulder_left": 2100, "shoulder_right": 2300,
              "minerals": ["kaolinite", "illite", "muscovite", "alunite", "montmorillonite"]},
    "Fe-OH": {"center": 2265, "shoulder_left": 2200, "shoulder_right": 2330,
              "minerals": ["jarosite", "nontronite"]},
    "Mg-OH": {"center": 2320, "shoulder_left": 2250, "shoulder_right": 2400,
              "minerals": ["chlorite", "serpentine", "talc"]},
    "CO3":   {"center": 2350, "shoulder_left": 2300, "shoulder_right": 2400,
              "minerals": ["calcite", "dolomite"]},
    "Fe3+":  {"center": 900, "shoulder_left": 750, "shoulder_right": 1050,
              "minerals": ["hematite", "goethite", "jarosite"]},
    "Fe2+":  {"center": 1050, "shoulder_left": 900, "shoulder_right": 1200,
              "minerals": ["biotite", "olivine", "pyroxene"]},
    "Water": {"center": 1450, "shoulder_left": 1350, "shoulder_right": 1550,
              "minerals": ["montmorillonite", "gypsum"]},
    "Water2":{"center": 1940, "shoulder_left": 1850, "shoulder_right": 2050,
              "minerals": ["montmorillonite", "gypsum"]},
}

# SAM classification thresholds (radians)
SAM_THRESHOLDS = {
    "high_confidence": 0.10,     # ~5.7 degrees
    "medium_confidence": 0.15,   # ~8.6 degrees
    "low_confidence": 0.20,      # ~11.5 degrees
    "reject": 0.30               # ~17.2 degrees - no match
}

# Unmixing constraints
UNMIXING_CONFIG = {
    "max_endmembers": 5,
    "min_abundance": 0.0,
    "max_reconstruction_error": 0.05,
    "method": "nnls"  # nnls or fcls
}
