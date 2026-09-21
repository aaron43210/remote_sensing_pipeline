# =============================================================
# OWNER: BAINTY KAUR
# =============================================================
"""
Surface Urban Heat Island (SUHI) Analysis
==========================================

Calculates Surface Urban Heat Island intensity from Landsat LST
using externally prepared urban and rural land-cover masks.

Definition
----------
    SUHI = mean(LST_urban) - mean(LST_rural)

Urban reference
---------------
The Bathinda workflow uses Dynamic World:

    Dynamic World Built class
    AND
    built probability >= configured threshold

Rural reference
---------------
The Bathinda workflow uses:

    Trees
    Grass
    Crops
    Shrub/Scrub

with:

    built probability < configured threshold

Dynamic World classes:
    1 = Trees
    2 = Grass
    4 = Crops
    5 = Shrub and scrub
    6 = Built

Important
---------
Dynamic World is treated as an independent land-cover reference,
not field-verified ground truth.

This module does NOT download data from Earth Engine. Urban/rural
masks must be supplied to the thermal service.

Sensitivity analysis
--------------------
The Bathinda workflow evaluates SUHI at:

    built probability = 0.40
    built probability = 0.50
    built probability = 0.60

The default production threshold is 0.50.
"""

import logging
from typing import Dict, Optional, Sequence, Tuple

import numpy as np

from config import ThermalConfig


logger = logging.getLogger(__name__)


class SUHIAnalyzer:
    """
    Calculate Surface Urban Heat Island intensity.

    The class works with:
        - LST raster
        - urban mask
        - rural mask

    All arrays must have the same spatial dimensions.
    """

    # =========================================================
    # INITIALIZATION
    # =========================================================

    def __init__(
        self,
        pixel_size_m: float = 30.0,
    ):
        """
        Parameters
        ----------
        pixel_size_m:
            Spatial resolution of the LST raster.

            Default:
                30 m

            This is the resolution used in the Bathinda
            Landsat 9 analysis.
        """

        if pixel_size_m <= 0:
            raise ValueError(
                "pixel_size_m must be greater than zero."
            )

        self.pixel_size_m = float(
            pixel_size_m
        )

    # =========================================================
    # MAIN SUHI CALCULATION
    # =========================================================

    def calculate(
        self,
        lst_celsius: np.ndarray,
        urban_mask: np.ndarray,
        rural_mask: np.ndarray,
    ) -> Tuple[float, Dict]:
        """
        Calculate SUHI intensity.

        Formula
        -------
            SUHI = mean urban LST - mean rural LST

        Parameters
        ----------
        lst_celsius:
            2D LST array in degrees Celsius.

        urban_mask:
            Boolean mask where True represents urban pixels.

        rural_mask:
            Boolean mask where True represents rural pixels.

        Returns
        -------
        suhi_intensity:
            SUHI intensity in degrees Celsius.

        details:
            Dictionary containing:
                - urban mean
                - rural mean
                - urban median
                - rural median
                - urban standard deviation
                - rural standard deviation
                - pixel counts
                - areas
                - SUHI intensity
        """

        lst = np.asarray(
            lst_celsius,
            dtype=np.float32,
        )

        urban = np.asarray(
            urban_mask,
            dtype=bool,
        )

        rural = np.asarray(
            rural_mask,
            dtype=bool,
        )

        self._validate_inputs(
            lst,
            urban,
            rural,
        )

        # -----------------------------------------------------
        # Valid LST pixels
        # -----------------------------------------------------

        valid_lst = (
            np.isfinite(lst)
            &
            (
                lst !=
                ThermalConfig.INPUT_NODATA
            )
        )

        # -----------------------------------------------------
        # Make sure urban/rural masks contain only valid LST
        # -----------------------------------------------------

        urban_valid = (
            urban &
            valid_lst
        )

        rural_valid = (
            rural &
            valid_lst
        )

        # -----------------------------------------------------
        # Prevent overlap
        # -----------------------------------------------------

        overlap = (
            urban_valid &
            rural_valid
        )

        if np.any(overlap):

            logger.warning(
                "Urban and rural masks overlap for %d pixels. "
                "Overlapping pixels will be excluded from both "
                "masks.",
                int(np.sum(overlap)),
            )

            urban_valid = (
                urban_valid &
                ~overlap
            )

            rural_valid = (
                rural_valid &
                ~overlap
            )

        # -----------------------------------------------------
        # Extract temperatures
        # -----------------------------------------------------

        urban_temperatures = lst[
            urban_valid
        ]

        rural_temperatures = lst[
            rural_valid
        ]

        if urban_temperatures.size == 0:

            raise ValueError(
                "No valid urban LST pixels were found."
            )

        if rural_temperatures.size == 0:

            raise ValueError(
                "No valid rural LST pixels were found."
            )

        # -----------------------------------------------------
        # Statistics
        # -----------------------------------------------------

        urban_mean = float(
            np.mean(
                urban_temperatures
            )
        )

        rural_mean = float(
            np.mean(
                rural_temperatures
            )
        )

        urban_median = float(
            np.median(
                urban_temperatures
            )
        )

        rural_median = float(
            np.median(
                rural_temperatures
            )
        )

        urban_std = float(
            np.std(
                urban_temperatures
            )
        )

        rural_std = float(
            np.std(
                rural_temperatures
            )
        )

        # -----------------------------------------------------
        # SUHI
        # -----------------------------------------------------

        suhi_intensity = (
            urban_mean -
            rural_mean
        )

        # -----------------------------------------------------
        # Areas
        # -----------------------------------------------------

        pixel_area_m2 = (
            self.pixel_size_m *
            self.pixel_size_m
        )

        urban_pixels = int(
            urban_temperatures.size
        )

        rural_pixels = int(
            rural_temperatures.size
        )

        urban_area_km2 = (
            urban_pixels *
            pixel_area_m2 /
            1_000_000.0
        )

        rural_area_km2 = (
            rural_pixels *
            pixel_area_m2 /
            1_000_000.0
        )

        # -----------------------------------------------------
        # Result
        # -----------------------------------------------------

        details = {
            "method": (
                "Mean urban LST minus mean rural LST"
            ),

            "urban_pixels": urban_pixels,

            "rural_pixels": rural_pixels,

            "urban_area_km2": float(
                urban_area_km2
            ),

            "rural_area_km2": float(
                rural_area_km2
            ),

            "urban_mean_lst_c": urban_mean,

            "rural_mean_lst_c": rural_mean,

            "urban_median_lst_c": urban_median,

            "rural_median_lst_c": rural_median,

            "urban_std_lst_c": urban_std,

            "rural_std_lst_c": rural_std,

            "suhi_intensity_c": float(
                suhi_intensity
            ),
        }

        logger.info(
            "SUHI calculated: "
            "urban_mean=%.4f°C, "
            "rural_mean=%.4f°C, "
            "SUHI=%.4f°C",
            urban_mean,
            rural_mean,
            suhi_intensity,
        )

        return (
            float(suhi_intensity),
            details,
        )

    # =========================================================
    # DYNAMIC WORLD MASK CREATION
    # =========================================================

    def create_masks_from_dynamic_world(
        self,
        dynamic_world_label: np.ndarray,
        dynamic_world_built_probability: np.ndarray,
        built_probability_threshold: Optional[float] = None,
    ) -> Tuple[np.ndarray, np.ndarray]:
        """
        Create urban and rural masks from Dynamic World data.

        Parameters
        ----------
        dynamic_world_label:
            Dynamic World class-label raster.

        dynamic_world_built_probability:
            Dynamic World built probability raster.

        built_probability_threshold:
            Probability threshold for built-up classification.

            Default:
                ThermalConfig.DW_BUILT_PROBABILITY_THRESHOLD

        Returns
        -------
        urban_mask:
            Boolean urban mask.

        rural_mask:
            Boolean rural reference mask.

        Dynamic World classes
        ---------------------
        1 = Trees
        2 = Grass
        4 = Crops
        5 = Shrub and scrub
        6 = Built
        """

        if built_probability_threshold is None:

            built_probability_threshold = (
                ThermalConfig
                .DW_BUILT_PROBABILITY_THRESHOLD
            )

        labels = np.asarray(
            dynamic_world_label
        )

        built_probability = np.asarray(
            dynamic_world_built_probability,
            dtype=np.float32,
        )

        if labels.shape != built_probability.shape:

            raise ValueError(
                "Dynamic World label and built-probability "
                "rasters must have identical shapes. "
                f"Received {labels.shape} and "
                f"{built_probability.shape}."
            )

        if not (
            0.0 <
            built_probability_threshold <=
            1.0
        ):

            raise ValueError(
                "built_probability_threshold must be "
                "between 0 and 1."
            )

        # -----------------------------------------------------
        # Valid Dynamic World pixels
        # -----------------------------------------------------

        valid_dw = (
            np.isfinite(
                built_probability
            )
            &
            (
                built_probability >= 0.0
            )
            &
            (
                built_probability <= 1.0
            )
        )

        # -----------------------------------------------------
        # Urban
        # -----------------------------------------------------
        #
        # Dynamic World Built class = 6
        # AND built probability >= threshold
        # -----------------------------------------------------

        urban_mask = (
            valid_dw
            &
            (
                labels ==
                ThermalConfig.DW_BUILT_CLASS
            )
            &
            (
                built_probability >=
                built_probability_threshold
            )
        )

        # -----------------------------------------------------
        # Rural
        # -----------------------------------------------------
        #
        # Trees, Grass, Crops, Shrub/Scrub
        # AND built probability < threshold
        # -----------------------------------------------------

        rural_classes = np.isin(
            labels,
            ThermalConfig.DW_RURAL_CLASSES,
        )

        rural_mask = (
            valid_dw
            &
            rural_classes
            &
            (
                built_probability <
                built_probability_threshold
            )
        )

        logger.info(
            "Dynamic World masks: "
            "threshold=%.2f, "
            "urban_px=%d, rural_px=%d",
            built_probability_threshold,
            int(np.sum(urban_mask)),
            int(np.sum(rural_mask)),
        )

        return (
            urban_mask.astype(bool),
            rural_mask.astype(bool),
        )

    # =========================================================
    # SUHI SENSITIVITY ANALYSIS
    # =========================================================

    def sensitivity_analysis(
        self,
        lst_celsius: np.ndarray,
        dynamic_world_label: np.ndarray,
        dynamic_world_built_probability: np.ndarray,
        thresholds: Optional[
            Sequence[float]
        ] = None,
    ) -> Dict:
        """
        Evaluate SUHI sensitivity to Dynamic World built
        probability thresholds.

        Default thresholds:

            0.40
            0.50
            0.60

        Returns
        -------
        dict
            Thresholds, SUHI values, and statistics for each
            threshold.
        """

        if thresholds is None:

            thresholds = (
                ThermalConfig
                .SUHI_SENSITIVITY_THRESHOLDS
            )

        thresholds = [
            float(value)
            for value in thresholds
        ]

        results = []

        for threshold in thresholds:

            urban_mask, rural_mask = (
                self.create_masks_from_dynamic_world(
                    dynamic_world_label,
                    dynamic_world_built_probability,
                    built_probability_threshold=threshold,
                )
            )

            suhi, details = self.calculate(
                lst_celsius,
                urban_mask,
                rural_mask,
            )

            results.append(
                {
                    "built_probability_threshold":
                        threshold,

                    "suhi_c":
                        float(suhi),

                    "urban_pixels":
                        details["urban_pixels"],

                    "rural_pixels":
                        details["rural_pixels"],

                    "urban_area_km2":
                        details["urban_area_km2"],

                    "rural_area_km2":
                        details["rural_area_km2"],

                    "urban_mean_lst_c":
                        details["urban_mean_lst_c"],

                    "rural_mean_lst_c":
                        details["rural_mean_lst_c"],
                }
            )

        # Convenient summary matching the Bathinda
        # sensitivity-analysis structure.

        summary = {
            "built_probability_thresholds": [
                item[
                    "built_probability_threshold"
                ]
                for item in results
            ],

            "suhi_c": [
                item["suhi_c"]
                for item in results
            ],

            "results": results,
        }

        logger.info(
            "SUHI sensitivity analysis completed: "
            "%s",
            summary["suhi_c"],
        )

        return summary

    # =========================================================
    # COMPLETE DYNAMIC WORLD SUHI ANALYSIS
    # =========================================================

    def analyze_dynamic_world_suhi(
        self,
        lst_celsius: np.ndarray,
        dynamic_world_label: np.ndarray,
        dynamic_world_built_probability: np.ndarray,
        threshold: Optional[float] = None,
        sensitivity: bool = True,
    ) -> Dict:
        """
        Complete Dynamic World-based SUHI analysis.

        This is the main high-level method intended for use by
        main.py.

        Returns
        -------
        dict
            Complete SUHI result including:

                - urban/rural definitions
                - urban/rural statistics
                - SUHI intensity
                - sensitivity analysis
        """

        if threshold is None:

            threshold = (
                ThermalConfig
                .DW_BUILT_PROBABILITY_THRESHOLD
            )

        # -----------------------------------------------------
        # Create masks
        # -----------------------------------------------------

        urban_mask, rural_mask = (
            self.create_masks_from_dynamic_world(
                dynamic_world_label,
                dynamic_world_built_probability,
                built_probability_threshold=threshold,
            )
        )

        # -----------------------------------------------------
        # Main SUHI
        # -----------------------------------------------------

        suhi, details = self.calculate(
            lst_celsius,
            urban_mask,
            rural_mask,
        )

        # -----------------------------------------------------
        # Result
        # -----------------------------------------------------

        result = {
            "method": (
                "Mean urban LST minus mean rural LST"
            ),

            "urban_definition": (
                "Dynamic World Built class with "
                "built probability >= "
                f"{threshold:.2f}"
            ),

            "rural_definition": (
                "Dynamic World Trees, Grass, Crops "
                "and Shrub/Scrub classes with "
                "built probability < "
                f"{threshold:.2f}"
            ),

            "urban_pixels":
                details["urban_pixels"],

            "urban_area_km2":
                details["urban_area_km2"],

            "urban_mean_lst_c":
                details["urban_mean_lst_c"],

            "urban_median_lst_c":
                details["urban_median_lst_c"],

            "urban_std_lst_c":
                details["urban_std_lst_c"],

            "rural_pixels":
                details["rural_pixels"],

            "rural_area_km2":
                details["rural_area_km2"],

            "rural_mean_lst_c":
                details["rural_mean_lst_c"],

            "rural_median_lst_c":
                details["rural_median_lst_c"],

            "rural_std_lst_c":
                details["rural_std_lst_c"],

            "suhi_intensity_c":
                details["suhi_intensity_c"],
        }

        # -----------------------------------------------------
        # Sensitivity analysis
        # -----------------------------------------------------

        if sensitivity:

            result[
                "suhi_sensitivity"
            ] = self.sensitivity_analysis(
                lst_celsius,
                dynamic_world_label,
                dynamic_world_built_probability,
            )

        return result

    # =========================================================
    # VALIDATION
    # =========================================================

    @staticmethod
    def _validate_inputs(
        lst: np.ndarray,
        urban_mask: np.ndarray,
        rural_mask: np.ndarray,
    ) -> None:
        """
        Validate LST and mask dimensions.
        """

        if lst.ndim != 2:

            raise ValueError(
                "LST raster must be a 2D array. "
                f"Received shape: {lst.shape}"
            )

        if urban_mask.ndim != 2:

            raise ValueError(
                "Urban mask must be a 2D array."
            )

        if rural_mask.ndim != 2:

            raise ValueError(
                "Rural mask must be a 2D array."
            )

        if lst.shape != urban_mask.shape:

            raise ValueError(
                "LST and urban mask must have the same "
                f"shape. Received {lst.shape} and "
                f"{urban_mask.shape}."
            )

        if lst.shape != rural_mask.shape:

            raise ValueError(
                "LST and rural mask must have the same "
                f"shape. Received {lst.shape} and "
                f"{rural_mask.shape}."
            )

        if lst.size == 0:

            raise ValueError(
                "Input LST raster is empty."
            )