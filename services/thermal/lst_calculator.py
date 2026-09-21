# =============================================================
# OWNER: BAINTY KAUR
# =============================================================
"""
Thermal Processing Service — LST Calculator

Handles Landsat thermal surface-temperature data.

Primary production input
------------------------
The upstream ingestion service provides Landsat Collection 2
Level-2 surface temperature as a float32 COG in degrees Celsius.

Therefore, when an L2 Celsius raster is received, this module
does NOT apply another emissivity correction.

Optional raw-DN support
-----------------------
If raw Landsat thermal DN values are supplied, the standard
Landsat Collection 2 scale and offset can be applied:

    T_kelvin = DN * 0.00341802 + 149.0

The resulting brightness/surface temperature can then optionally
be passed through an emissivity correction when explicitly
requested.

Thermal classification
----------------------
The service uses five classes based on scene statistics:

    1 = Very Low
    2 = Low
    3 = Moderate
    4 = High
    5 = Very High

Thresholds are based on:

    mean - 2σ
    mean - σ
    mean + σ
    mean + 2σ

Global and local anomaly detection are handled by
anomaly_detector.py.
"""

import logging
from typing import Optional

import numpy as np

from config import ThermalConfig


logger = logging.getLogger(__name__)


class LSTCalculator:
    """
    Process Landsat surface-temperature data.

    Main production workflow:

        L2 Celsius raster
                ↓
        validate / mask NoData
                ↓
        thermal statistics
                ↓
        5-class classification

    The calculator does not automatically perform emissivity
    correction on an already processed Landsat C2 L2 surface
    temperature product.
    """

    # =========================================================
    # PHYSICAL CONSTANTS
    # =========================================================

    # Landsat 9 TIRS Band 10 centre wavelength
    WAVELENGTH_BAND10 = 10.9e-6  # metres

    PLANCK_H = 6.62607015e-34     # J·s
    BOLTZMANN_K = 1.380649e-23    # J/K
    SPEED_OF_LIGHT = 299792458.0  # m/s

    # rho = h*c/k
    RHO = (
        PLANCK_H *
        SPEED_OF_LIGHT /
        BOLTZMANN_K
    )

    # =========================================================
    # PHYSICAL VALIDITY RANGE
    # =========================================================

    LST_MIN_C = -50.0
    LST_MAX_C = 70.0

    # =========================================================
    # LANDSAT C2 L2 SCALE / OFFSET
    # =========================================================

    DEFAULT_SCALE = 0.00341802
    DEFAULT_OFFSET = 149.0

    # =========================================================
    # PUBLIC METHODS
    # =========================================================

    def lst_from_celsius(
        self,
        celsius: np.ndarray,
        emissivity: Optional[np.ndarray] = None,
        apply_emissivity: bool = False,
    ) -> np.ndarray:
        """
        Validate and return Landsat L2 surface temperature.

        Parameters
        ----------
        celsius:
            Surface temperature array in degrees Celsius.

        emissivity:
            Optional emissivity array.

        apply_emissivity:
            Whether to explicitly apply the single-channel
            emissivity correction.

            IMPORTANT:
            False by default because Landsat C2 L2 surface
            temperature has already undergone the required
            surface-temperature processing.

        Returns
        -------
        np.ndarray
            LST in degrees Celsius with invalid values masked
            as NaN.
        """

        celsius = np.asarray(
            celsius,
            dtype=np.float32
        ).copy()

        self._validate_array(celsius)

        # Mask input NoData
        celsius = self._mask_input_nodata(celsius)

        # Normally return the L2 surface temperature directly.
        if not apply_emissivity:
            return self._mask_invalid(celsius)

        # Explicit emissivity correction is available only when
        # requested.
        if emissivity is None:
            logger.warning(
                "Emissivity correction requested but no emissivity "
                "array was provided. Returning uncorrected L2 LST."
            )
            return self._mask_invalid(celsius)

        kelvin = celsius + 273.15

        lst_kelvin = self._apply_emissivity(
            kelvin,
            emissivity
        )

        lst_celsius = lst_kelvin - 273.15

        return self._mask_invalid(lst_celsius)

    # =========================================================

    def lst_from_kelvin(
        self,
        kelvin: np.ndarray,
        emissivity: Optional[np.ndarray] = None,
        apply_emissivity: bool = False,
    ) -> np.ndarray:
        """
        Convert Kelvin temperature to Celsius.

        Parameters
        ----------
        kelvin:
            Temperature array in Kelvin.

        emissivity:
            Optional emissivity array.

        apply_emissivity:
            Explicitly apply emissivity correction.

        Returns
        -------
        np.ndarray
            Temperature in degrees Celsius.
        """

        kelvin = np.asarray(
            kelvin,
            dtype=np.float32
        ).copy()

        self._validate_array(kelvin)

        if apply_emissivity:

            if emissivity is None:
                logger.warning(
                    "Emissivity correction requested but no "
                    "emissivity array was provided."
                )
            else:
                kelvin = self._apply_emissivity(
                    kelvin,
                    emissivity
                )

        lst_celsius = kelvin - 273.15

        return self._mask_invalid(lst_celsius)

    # =========================================================

    def lst_from_dn(
        self,
        dn: np.ndarray,
        scale: Optional[float] = None,
        offset: Optional[float] = None,
        emissivity: Optional[np.ndarray] = None,
        apply_emissivity: bool = False,
    ) -> np.ndarray:
        """
        Convert Landsat thermal DN values to Celsius.

        Landsat Collection 2 Level-2 conversion:

            T_kelvin = DN * scale + offset

        Default:

            scale  = 0.00341802
            offset = 149.0

        Parameters
        ----------
        dn:
            Raw thermal DN array.

        scale:
            Landsat scale factor.

        offset:
            Landsat thermal offset.

        emissivity:
            Optional emissivity array.

        apply_emissivity:
            Explicitly apply emissivity correction.

        Returns
        -------
        np.ndarray
            Temperature in degrees Celsius.
        """

        if scale is None:
            scale = self.DEFAULT_SCALE

        if offset is None:
            offset = self.DEFAULT_OFFSET

        dn = np.asarray(
            dn,
            dtype=np.float32
        )

        self._validate_array(dn)

        kelvin = (
            dn * float(scale) +
            float(offset)
        )

        return self.lst_from_kelvin(
            kelvin,
            emissivity=emissivity,
            apply_emissivity=apply_emissivity,
        )

    # =========================================================
    # THERMAL STATISTICS + CLASSIFICATION
    # =========================================================

    def compute_thermal_indices(
        self,
        lst_celsius: np.ndarray,
    ) -> dict:
        """
        Compute thermal statistics and five-class
        thermal classification.

        Classification
        --------------
        1 = Very Low
            LST < mean - 2σ

        2 = Low
            mean - 2σ <= LST < mean - σ

        3 = Moderate
            mean - σ <= LST < mean + σ

        4 = High
            mean + σ <= LST < mean + 2σ

        5 = Very High
            LST >= mean + 2σ

        Returns
        -------
        dict
            Scene statistics and classification array.
        """

        lst = np.asarray(
            lst_celsius,
            dtype=np.float32
        )

        self._validate_array(lst)

        # -----------------------------------------------------
        # Valid-pixel mask
        # -----------------------------------------------------

        valid_mask = self._valid_mask(lst)

        valid_values = lst[valid_mask]

        if valid_values.size == 0:

            logger.warning(
                "No valid LST pixels were found."
            )

            empty_classification = np.zeros(
                lst.shape,
                dtype=np.uint8
            )

            empty_anomaly = np.full(
                lst.shape,
                ThermalConfig.OUTPUT_NODATA,
                dtype=np.float32
            )

            return {
                "mean": 0.0,
                "std": 0.0,
                "min": 0.0,
                "max": 0.0,
                "n_valid_pixels": 0,
                "thermal_anomaly": empty_anomaly,
                "classification": empty_classification,
                "classification_thresholds": {},
                "classification_labels": self._classification_labels(),
            }

        # -----------------------------------------------------
        # Scene statistics
        # -----------------------------------------------------

        mean_t = float(
            np.mean(valid_values)
        )

        std_t = float(
            np.std(valid_values)
        )

        min_t = float(
            np.min(valid_values)
        )

        max_t = float(
            np.max(valid_values)
        )

        # -----------------------------------------------------
        # Thresholds
        # -----------------------------------------------------

        threshold_minus_2sigma = (
            mean_t - 2.0 * std_t
        )

        threshold_minus_1sigma = (
            mean_t - std_t
        )

        threshold_plus_1sigma = (
            mean_t + std_t
        )

        threshold_plus_2sigma = (
            mean_t + 2.0 * std_t
        )

        # -----------------------------------------------------
        # Thermal anomaly
        # -----------------------------------------------------
        #
        # This is temperature deviation from the scene mean.
        #
        # Z-score based anomaly detection is handled separately
        # by anomaly_detector.py.
        # -----------------------------------------------------

        thermal_anomaly = np.full(
            lst.shape,
            ThermalConfig.OUTPUT_NODATA,
            dtype=np.float32
        )

        thermal_anomaly[valid_mask] = (
            lst[valid_mask] - mean_t
        )

        # -----------------------------------------------------
        # Five-class classification
        # -----------------------------------------------------

        classification = np.zeros(
            lst.shape,
            dtype=np.uint8
        )

        # Class 1 — Very Low
        classification[
            valid_mask &
            (lst < threshold_minus_2sigma)
        ] = ThermalConfig.CLASS_VERY_LOW

        # Class 2 — Low
        classification[
            valid_mask &
            (lst >= threshold_minus_2sigma) &
            (lst < threshold_minus_1sigma)
        ] = ThermalConfig.CLASS_LOW

        # Class 3 — Moderate
        classification[
            valid_mask &
            (lst >= threshold_minus_1sigma) &
            (lst < threshold_plus_1sigma)
        ] = ThermalConfig.CLASS_MODERATE

        # Class 4 — High
        classification[
            valid_mask &
            (lst >= threshold_plus_1sigma) &
            (lst < threshold_plus_2sigma)
        ] = ThermalConfig.CLASS_HIGH

        # Class 5 — Very High
        classification[
            valid_mask &
            (lst >= threshold_plus_2sigma)
        ] = ThermalConfig.CLASS_VERY_HIGH

        # -----------------------------------------------------
        # Logging
        # -----------------------------------------------------

        logger.info(
            "Thermal statistics: "
            "mean=%.4f°C, std=%.4f°C, "
            "min=%.4f°C, max=%.4f°C, valid_px=%d",
            mean_t,
            std_t,
            min_t,
            max_t,
            int(valid_values.size),
        )

        logger.info(
            "Thermal thresholds: "
            "mean-2σ=%.4f°C, "
            "mean-σ=%.4f°C, "
            "mean+σ=%.4f°C, "
            "mean+2σ=%.4f°C",
            threshold_minus_2sigma,
            threshold_minus_1sigma,
            threshold_plus_1sigma,
            threshold_plus_2sigma,
        )

        return {
            "mean": mean_t,
            "std": std_t,
            "min": min_t,
            "max": max_t,

            "n_valid_pixels": int(
                valid_values.size
            ),

            "thermal_anomaly": thermal_anomaly,

            "classification": classification,

            "classification_thresholds": {
                "very_low_upper": threshold_minus_2sigma,
                "low_upper": threshold_minus_1sigma,
                "moderate_upper": threshold_plus_1sigma,
                "high_upper": threshold_plus_2sigma,
            },

            "classification_labels": (
                self._classification_labels()
            ),
        }

    # =========================================================
    # VALIDITY HELPERS
    # =========================================================

    def _valid_mask(
        self,
        lst_celsius: np.ndarray,
    ) -> np.ndarray:
        """
        Return valid LST pixels.

        Current project convention:
            0 = NoData

        Also removes:
            NaN
            +inf
            -inf
            physically unrealistic temperatures
        """

        valid = np.isfinite(lst_celsius)

        # Current Bathinda LST convention
        valid &= (
            lst_celsius != ThermalConfig.INPUT_NODATA
        )

        valid &= (
            lst_celsius >= self.LST_MIN_C
        )

        valid &= (
            lst_celsius <= self.LST_MAX_C
        )

        return valid

    # =========================================================

    def _mask_input_nodata(
        self,
        celsius: np.ndarray,
    ) -> np.ndarray:
        """
        Convert input NoData values to NaN.

        This keeps NoData from contaminating statistics.
        """

        result = celsius.copy()

        result[
            result == ThermalConfig.INPUT_NODATA
        ] = np.nan

        return result

    # =========================================================

    def _mask_invalid(
        self,
        lst_celsius: np.ndarray,
    ) -> np.ndarray:
        """
        Replace physically invalid temperatures with NaN.
        """

        result = np.asarray(
            lst_celsius,
            dtype=np.float32
        ).copy()

        invalid = (
            ~np.isfinite(result)
            |
            (result < self.LST_MIN_C)
            |
            (result > self.LST_MAX_C)
        )

        result[invalid] = np.nan

        return result

    # =========================================================
    # EMISSIVITY CORRECTION
    # =========================================================

    def _apply_emissivity(
        self,
        kelvin: np.ndarray,
        emissivity: np.ndarray,
    ) -> np.ndarray:
        """
        Apply single-channel emissivity correction.

        This method is NOT automatically applied to Landsat
        C2 L2 surface temperature.

        It exists for cases where the service explicitly
        receives brightness temperature and an emissivity
        estimate and needs to perform an independent LST
        retrieval.
        """

        kelvin = np.asarray(
            kelvin,
            dtype=np.float32
        )

        emissivity = np.asarray(
            emissivity,
            dtype=np.float32
        )

        if kelvin.shape != emissivity.shape:
            raise ValueError(
                "Temperature and emissivity arrays must have "
                f"the same shape. Received "
                f"{kelvin.shape} and {emissivity.shape}."
            )

        # Prevent log(0) and unrealistic emissivity values.
        emissivity = np.clip(
            emissivity,
            0.90,
            0.999
        )

        correction = (
            self.WAVELENGTH_BAND10 /
            self.RHO
        ) * kelvin * np.log(emissivity)

        denominator = 1.0 + correction

        # Protect against numerical problems.
        denominator = np.where(
            np.abs(denominator) < 1e-10,
            np.nan,
            denominator
        )

        lst_kelvin = (
            kelvin /
            denominator
        )

        return lst_kelvin.astype(
            np.float32
        )

    # =========================================================
    # VALIDATION
    # =========================================================

    @staticmethod
    def _validate_array(
        array: np.ndarray,
    ) -> None:
        """
        Validate that the input is a non-empty 2D array.
        """

        if array.ndim != 2:
            raise ValueError(
                "Thermal raster must be a 2D array. "
                f"Received shape: {array.shape}"
            )

        if array.size == 0:
            raise ValueError(
                "Thermal raster is empty."
            )

    # =========================================================
    # CLASS LABELS
    # =========================================================

    @staticmethod
    def _classification_labels() -> dict:
        """
        Return human-readable thermal class labels.
        """

        return {
            0: "No Data",
            1: "Very Low",
            2: "Low",
            3: "Moderate",
            4: "High",
            5: "Very High",
        }