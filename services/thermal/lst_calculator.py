# =============================================================
# OWNER: BAINTY KAUR
# =============================================================
"""
Land Surface Temperature (LST) Calculator

Implements physics-based LST retrieval from thermal radiance data
(as downloaded and pre-converted by the ingestion service).

Physics
-------
For Landsat Collection 2 Level-2, the ingestion service provides
data already converted to Celsius (stored as float32 COG in MinIO).

If raw DN data is provided, the standard Landsat C2 L2 conversion is:
    Kelvin = DN × 0.00341802 + 149.0

Emissivity correction (single-channel method):
    LST = T_b / (1 + (λ/ρ) × T_b × ln(ε))

Where:
    T_b = brightness temperature in Kelvin
    λ   = 10.9e-6 m  (Landsat 9 TIRS Band 10 centre wavelength)
    ρ   = h × c / k  = 1.4388e-2 m·K
    ε   = land surface emissivity

References
----------
- Jiménez-Muñoz & Sobrino (2003): "A generalised single-channel
  method for retrieving LST from remote sensing data." IEEE TGRS.
- Cook et al. (2014): "Landsat TIRS Surface Temperature Algorithm."
  JSTAR.
- Li et al. (2013): "Satellite-derived LST: Current status and
  perspectives." Remote Sensing of Environment, 131, 14-37.
"""

import numpy as np
import logging

logger = logging.getLogger(__name__)


class LSTCalculator:
    """
    Convert Landsat brightness temperature → Land Surface Temperature.

    Inputs come from the ingestion service via MinIO.
    Bainty reads the COG, runs this calculator, and publishes results.
    """

    # ── Physical constants ───────────────────────────────────────────────
    WAVELENGTH_BAND10 = 10.9e-6          # metres  (TIRS Band 10 centre)
    PLANCK_H          = 6.626e-34        # J·s
    BOLTZMANN_K       = 1.381e-23        # J/K
    SPEED_OF_LIGHT    = 3.0e8            # m/s
    # ρ = h × c / k = 1.4388e-2 m·K
    RHO = PLANCK_H * SPEED_OF_LIGHT / BOLTZMANN_K

    # ── Validity range ───────────────────────────────────────────────────
    LST_MIN_C = -50.0   # °C  (arctic surface minimum)
    LST_MAX_C =  70.0   # °C  (extreme desert maximum)

    # ── Landsat C2 L2 default scale/offset ──────────────────────────────
    DEFAULT_SCALE  = 0.00341802
    DEFAULT_OFFSET = 149.0       # result is Kelvin

    def lst_from_celsius(self, celsius: np.ndarray,
                         emissivity: np.ndarray = None) -> np.ndarray:
        """
        Apply emissivity correction to data already in Celsius.

        The ingestion service stores data in Celsius. This method
        converts back to Kelvin, applies emissivity correction, then
        returns corrected Celsius.

        Args:
            celsius:    (rows, cols) float32 surface temperature in °C.
            emissivity: (rows, cols) float32 emissivity [0.90, 0.995]. Optional.

        Returns:
            lst_celsius: (rows, cols) emissivity-corrected LST in °C.
        """
        celsius = np.asarray(celsius, dtype=np.float32)
        kelvin = celsius + 273.15
        lst_kelvin = self._apply_emissivity(kelvin, emissivity)
        lst_celsius = lst_kelvin - 273.15
        return self._mask_invalid(lst_celsius)

    def lst_from_kelvin(self, kelvin: np.ndarray,
                        emissivity: np.ndarray = None) -> np.ndarray:
        """
        Convert brightness temperature (Kelvin) → LST (°C).

        Args:
            kelvin:     (rows, cols) brightness temperature in K.
            emissivity: (rows, cols) emissivity array. Optional.

        Returns:
            lst_celsius: (rows, cols) LST in °C.
        """
        kelvin = np.asarray(kelvin, dtype=np.float32)
        lst_kelvin = self._apply_emissivity(kelvin, emissivity)
        lst_celsius = lst_kelvin - 273.15
        return self._mask_invalid(lst_celsius)

    def lst_from_dn(self, dn: np.ndarray,
                    scale: float = None,
                    offset: float = None,
                    emissivity: np.ndarray = None) -> np.ndarray:
        """
        Convert raw Digital Numbers → LST (°C).

        Useful if ingestion sends raw DN rather than converted values.

        Args:
            dn:         (rows, cols) uint16 raw digital numbers.
            scale:      Scale factor from MTL file. Default: 0.00341802.
            offset:     Add offset from MTL file. Default: 149.0 K.
            emissivity: (rows, cols) emissivity. Optional.

        Returns:
            lst_celsius: (rows, cols) LST in °C.
        """
        scale  = scale  if scale  is not None else self.DEFAULT_SCALE
        offset = offset if offset is not None else self.DEFAULT_OFFSET

        dn = np.asarray(dn, dtype=np.float32)
        kelvin = dn * scale + offset
        return self.lst_from_kelvin(kelvin, emissivity)

    def compute_thermal_indices(self, lst_celsius: np.ndarray) -> dict:
        """
        Compute scene-level thermal statistics and classification map.

        Classification:
            1 = Very Cold (< mean - 2σ)
            2 = Cold      (mean - 2σ  to  mean - σ)
            3 = Normal    (mean ± σ)
            4 = Warm      (mean + σ   to  mean + 2σ)
            5 = Hot       (> mean + 2σ)

        Args:
            lst_celsius: (rows, cols) LST in °C.

        Returns:
            dict with statistics + 2D arrays: anomaly, classification.
        """
        valid = lst_celsius[(~np.isnan(lst_celsius)) & (lst_celsius != 0)]

        if len(valid) == 0:
            empty = np.zeros_like(lst_celsius)
            return {
                'mean': 0.0, 'std': 0.0, 'min': 0.0, 'max': 0.0,
                'thermal_anomaly': empty,
                'classification': empty.astype(np.int8),
                'n_valid_pixels': 0,
            }

        mean_t = float(np.nanmean(lst_celsius))
        std_t  = float(np.nanstd(lst_celsius))

        # Per-pixel deviation from scene mean
        anomaly = (lst_celsius - mean_t).astype(np.float32)

        # 5-class temperature classification
        cls = np.zeros_like(lst_celsius, dtype=np.int8)
        cls[lst_celsius < mean_t - 2 * std_t] = 1
        cls[(lst_celsius >= mean_t - 2 * std_t) &
            (lst_celsius <  mean_t - std_t)]   = 2
        cls[(lst_celsius >= mean_t - std_t) &
            (lst_celsius <= mean_t + std_t)]   = 3
        cls[(lst_celsius >  mean_t + std_t) &
            (lst_celsius <= mean_t + 2 * std_t)] = 4
        cls[lst_celsius >  mean_t + 2 * std_t]   = 5

        logger.info(
            "Thermal indices: mean=%.1f°C, std=%.1f°C, "
            "min=%.1f°C, max=%.1f°C, valid_px=%d",
            mean_t, std_t,
            float(np.nanmin(lst_celsius)),
            float(np.nanmax(lst_celsius)),
            int(len(valid)),
        )

        return {
            'mean': mean_t,
            'std':  std_t,
            'min':  float(np.nanmin(lst_celsius)),
            'max':  float(np.nanmax(lst_celsius)),
            'thermal_anomaly': anomaly,
            'classification': cls,
            'classification_labels': {
                0: 'No Data',
                1: 'Very Cold (< -2σ)',
                2: 'Cold (-2σ to -σ)',
                3: 'Normal (±σ)',
                4: 'Warm (+σ to +2σ)',
                5: 'Hot (> +2σ)',
            },
            'n_valid_pixels': int(len(valid)),
        }

    # ── Private helpers ──────────────────────────────────────────────────

    def _apply_emissivity(self, kelvin: np.ndarray,
                          emissivity: np.ndarray = None) -> np.ndarray:
        """Apply single-channel emissivity correction."""
        if emissivity is None:
            return kelvin

        emissivity = np.asarray(emissivity, dtype=np.float32)
        # Avoid log(0)
        emissivity = np.clip(emissivity, 1e-6, 1.0)

        correction = (self.WAVELENGTH_BAND10 / self.RHO) * kelvin * np.log(emissivity)
        lst_kelvin = kelvin / (1.0 + correction)
        return lst_kelvin

    def _mask_invalid(self, lst_celsius: np.ndarray) -> np.ndarray:
        """NaN-mask physically unrealistic values."""
        lst_celsius[lst_celsius < self.LST_MIN_C] = np.nan
        lst_celsius[lst_celsius > self.LST_MAX_C] = np.nan
        return lst_celsius
