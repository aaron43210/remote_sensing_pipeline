# =============================================================
# OWNER: ANANTAHANARAYANAN
# =============================================================
"""
Landsat Collection 2 Level-2 preprocessing constants.

Landsat 8/9 TIRS Band 10: 10.9 um, 30 m (resampled from 100 m), 16-day revisit.

References:
    USGS Landsat Collection 2 Level-2 Science Product Guide (2021)
    Cook et al. (2014). Landsat TIRS Surface Temperature algorithm. JSTAR.
"""

import os

# ── DN -> Kelvin (Landsat C2 L2 Surface Temperature) ─────────────────────
# Read from the scene's MTL file when present; these are the documented
# fallbacks.
ST_SCALE = 0.00341802            # Kelvin per DN
ST_OFFSET = 149.0                # additive offset, Kelvin
KELVIN_TO_CELSIUS = 273.15

# ── Physical plausibility bounds (degrees Celsius) ───────────────────────
# Land surface temperature outside this range is a sensor or cloud artefact,
# not a real measurement. Masked to NaN, then to FILL_VALUE for storage.
TEMP_MIN_C = float(os.getenv("THERMAL_TEMP_MIN_C", "-50"))
TEMP_MAX_C = float(os.getenv("THERMAL_TEMP_MAX_C", "70"))

# ── Fill value for invalid pixels ────────────────────────────────────────
# 0.0 by request of the thermal service. NOTE: 0 C is a PLAUSIBLE
# temperature, so fill is indistinguishable from a reading unless the nodata
# tag is honoured -- cog.write() declares it, and every statistic excludes
# it. On a real Punjab scene 31.7% of the raster is empty UTM corner: a naive
# mean gives 23.3 C against a true 34.1 C.
FILL_VALUE = float(os.getenv("THERMAL_FILL_VALUE", "0.0"))

# ── Output raster ────────────────────────────────────────────────────────
BLOCK_SIZE = 256
COMPRESSION = "lzw"
