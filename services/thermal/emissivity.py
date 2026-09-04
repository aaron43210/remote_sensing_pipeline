# =============================================================
# OWNER: BAINTY KAUR
# =============================================================
"""
Land Surface Emissivity Estimation

Emissivity (ε) is the ratio of radiation emitted by a surface to that
emitted by a perfect blackbody at the same temperature.
It is critical for accurate LST: without emissivity correction,
LST can be off by 1–3°C.

This module implements NDVI-based emissivity estimation, the standard
physics-based approach validated for Landsat thermal bands.

Theory
------
The vegetation fraction (Pv) is estimated from NDVI:
    Pv = ((NDVI - NDVI_min) / (NDVI_max - NDVI_min)) ^ 2

Surface emissivity is then:
    ε = ε_soil + Pv × (ε_veg - ε_soil)

Where:
    ε_soil = 0.9625  (bare dry soil)
    ε_veg  = 0.986   (dense green vegetation)
    NDVI_min = 0.05  (pure bare soil)
    NDVI_max = 0.70  (dense vegetation)

References
----------
- Sobrino et al. (2004): "Land surface temperature retrieval
  from LANDSAT TM 5." Remote Sensing of Environment, 90, 434-440.
- Valor & Caselles (1996): "Mapping land surface emissivity
  from NDVI." Remote Sensing of Environment, 57, 167-184.
- Van de Griend & Owe (1993): "On the relationship between
  thermal emissivity and NDVI." Int. J. Remote Sensing, 14, 1119-1131.
"""

import numpy as np
import logging

logger = logging.getLogger(__name__)


class EmissivityCalculator:
    """
    Estimate land surface emissivity from NDVI.

    Input : NDVI array (rows × cols), float32
    Output: Emissivity array (rows × cols), float32, range [0.90, 0.995]
    """

    # Standard emissivity reference values (Sobrino et al. 2004)
    EPSILON_SOIL = 0.9625   # Bare dry soil emissivity
    EPSILON_VEG  = 0.986    # Dense full-canopy vegetation emissivity
    NDVI_MIN     = 0.05     # NDVI below this = bare soil
    NDVI_MAX     = 0.70     # NDVI above this = full vegetation

    def estimate_from_ndvi(self, ndvi: np.ndarray) -> np.ndarray:
        """
        Estimate per-pixel emissivity from NDVI.

        Args:
            ndvi: (rows, cols) NDVI array. Values typically in [-1, 1].

        Returns:
            emissivity: (rows, cols) float32 array in range [0.90, 0.995].
        """
        ndvi = np.asarray(ndvi, dtype=np.float32)

        # ── Vegetation fraction ──────────────────────────────────────────
        # Pv = 0 → bare soil, Pv = 1 → dense vegetation
        ndvi_range = self.NDVI_MAX - self.NDVI_MIN  # 0.65
        pv = ((ndvi - self.NDVI_MIN) / ndvi_range) ** 2
        pv = np.clip(pv, 0.0, 1.0)

        # ── Emissivity ───────────────────────────────────────────────────
        emissivity = self.EPSILON_SOIL + pv * (self.EPSILON_VEG - self.EPSILON_SOIL)

        # Clip to physically valid range
        emissivity = np.clip(emissivity, 0.90, 0.995)

        logger.debug(
            "Emissivity estimated: min=%.4f, max=%.4f, mean=%.4f",
            float(np.nanmin(emissivity)),
            float(np.nanmax(emissivity)),
            float(np.nanmean(emissivity)),
        )

        return emissivity

    def water_mask(self, ndvi: np.ndarray, threshold: float = -0.1) -> np.ndarray:
        """
        Create a boolean water mask from NDVI.

        Water bodies have very low or negative NDVI.

        Args:
            ndvi: (rows, cols) NDVI array.
            threshold: NDVI below this value → water.

        Returns:
            mask: (rows, cols) bool array. True = water pixel.
        """
        return ndvi < threshold

    def urban_estimate(self, emissivity_map: np.ndarray,
                       urban_mask: np.ndarray) -> np.ndarray:
        """
        Apply a fixed lower emissivity for urban/built-up pixels.

        Urban surfaces (concrete, asphalt) have lower emissivity (~0.924).

        Args:
            emissivity_map: (rows, cols) emissivity array.
            urban_mask: (rows, cols) bool array. True = urban pixel.

        Returns:
            corrected: (rows, cols) emissivity with urban correction applied.
        """
        corrected = emissivity_map.copy()
        corrected[urban_mask] = 0.924
        return corrected
