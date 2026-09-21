# =============================================================
# OWNER: BAINTY KAUR
# =============================================================
"""
Thermal Anomaly Detection

Statistical and spatial analysis of Landsat LST.

Implemented methods
-------------------
1. Global Z-score anomaly detection
2. Local moving-window Z-score anomaly detection
3. Thermal hotspot detection
4. Connected-component filtering

Methodology
-----------
Global anomaly:
    Z = (LST - scene_mean) / scene_std

    Hot anomaly:
        Z >= GLOBAL_Z_THRESHOLD

    Cold anomaly:
        Z <= -GLOBAL_Z_THRESHOLD

Local anomaly:
    11 x 11 pixel neighbourhood by default.

    Local statistics are calculated using only valid pixels,
    so NoData values do not contaminate the local mean/std.

Hotspots:
    1. Calculate the 95th percentile of valid LST.
    2. Select pixels >= percentile threshold.
    3. Apply 8-connected component analysis.
    4. Remove components smaller than 9 pixels.

At 30 m resolution:

    9 pixels = 9 x 30 x 30 m²
             = 8,100 m²
             = 0.81 hectares

Urban Heat Island / SUHI
------------------------
SUHI is handled separately in suhi.py because it requires
urban/rural land-cover masks and is therefore kept separate
from purely statistical anomaly detection.
"""

# =============================================================
# IMPORTS
# =============================================================

import logging

import numpy as np
from scipy import ndimage

from config import ThermalConfig


# =============================================================
# LOGGING
# =============================================================

logger = logging.getLogger(__name__)


# =============================================================
# THERMAL ANOMALY DETECTOR
# =============================================================

class ThermalAnomalyDetector:
    """
    Statistical thermal anomaly detector for LST rasters.

    The detector does not perform LST retrieval itself.
    It receives an LST array in degrees Celsius.
    """

    # =========================================================
    # GLOBAL Z-SCORE
    # =========================================================

    def detect_global_anomalies(
        self,
        lst_celsius: np.ndarray,
        z_threshold: float = None,
    ):
        """
        Detect scene-wide thermal anomalies using Z-score.

        Formula
        -------
            Z = (LST - mean) / std

        Hot anomaly:
            Z >= z_threshold

        Cold anomaly:
            Z <= -z_threshold

        Parameters
        ----------
        lst_celsius:
            2D LST array in degrees Celsius.

        z_threshold:
            Z-score threshold.
            Defaults to ThermalConfig.GLOBAL_Z_THRESHOLD.

        Returns
        -------
        z_scores:
            Float32 Z-score raster.

        hot_mask:
            Boolean raster containing positive thermal anomalies.

        cold_mask:
            Boolean raster containing negative thermal anomalies.

        statistics:
            Dictionary containing mean/std/threshold information.
        """

        if z_threshold is None:
            z_threshold = ThermalConfig.GLOBAL_Z_THRESHOLD

        lst = np.asarray(
            lst_celsius,
            dtype=np.float32,
        )

        self._validate_array(lst)

        valid_mask = self._valid_mask(lst)

        valid_values = lst[valid_mask]

        # -----------------------------------------------------
        # No valid pixels
        # -----------------------------------------------------

        if valid_values.size == 0:

            logger.warning(
                "Global anomaly detection: no valid pixels."
            )

            z_scores = self._empty_float_output(
                lst.shape
            )

            hot_mask = np.zeros(
                lst.shape,
                dtype=bool,
            )

            cold_mask = np.zeros(
                lst.shape,
                dtype=bool,
            )

            statistics = {
                "mean": None,
                "std": None,
                "threshold": float(z_threshold),
                "hot_pixels": 0,
                "cold_pixels": 0,
            }

            return (
                z_scores,
                hot_mask,
                cold_mask,
                statistics,
            )

        # -----------------------------------------------------
        # Scene statistics
        # -----------------------------------------------------

        mean = float(
            np.mean(valid_values)
        )

        std = float(
            np.std(valid_values)
        )

        # -----------------------------------------------------
        # Zero / near-zero variance
        # -----------------------------------------------------

        if std < 1e-10:

            logger.warning(
                "Global anomaly detection: scene standard "
                "deviation is effectively zero."
            )

            z_scores = self._empty_float_output(
                lst.shape
            )

            hot_mask = np.zeros(
                lst.shape,
                dtype=bool,
            )

            cold_mask = np.zeros(
                lst.shape,
                dtype=bool,
            )

            statistics = {
                "mean": mean,
                "std": std,
                "threshold": float(z_threshold),
                "hot_pixels": 0,
                "cold_pixels": 0,
            }

            return (
                z_scores,
                hot_mask,
                cold_mask,
                statistics,
            )

        # -----------------------------------------------------
        # Calculate global Z-score
        # -----------------------------------------------------

        z_scores = np.full(
            lst.shape,
            ThermalConfig.OUTPUT_NODATA,
            dtype=np.float32,
        )

        z_scores[valid_mask] = (
            (
                lst[valid_mask] - mean
            ) / std
        ).astype(np.float32)

        # -----------------------------------------------------
        # Hot / cold anomaly masks
        # -----------------------------------------------------

        hot_mask = (
            valid_mask
            & (z_scores >= z_threshold)
        )

        cold_mask = (
            valid_mask
            & (z_scores <= -z_threshold)
        )

        logger.info(
            "Global anomaly detection: "
            "mean=%.4f°C, std=%.4f°C, "
            "threshold=±%.2fσ, "
            "hot_px=%d, cold_px=%d",
            mean,
            std,
            z_threshold,
            int(np.sum(hot_mask)),
            int(np.sum(cold_mask)),
        )

        statistics = {
            "mean": mean,
            "std": std,
            "threshold": float(z_threshold),
            "hot_pixels": int(
                np.sum(hot_mask)
            ),
            "cold_pixels": int(
                np.sum(cold_mask)
            ),
        }

        return (
            z_scores,
            hot_mask,
            cold_mask,
            statistics,
        )

    # =========================================================
    # LOCAL Z-SCORE
    # =========================================================

    def detect_local_anomalies(
        self,
        lst_celsius: np.ndarray,
        window_size: int = None,
        z_threshold: float = None,
    ):
        """
        Detect local thermal anomalies using a moving window.

        Default:
            window = 11 x 11
            threshold = 2σ

        NoData pixels are excluded from local statistics.

        Parameters
        ----------
        lst_celsius:
            2D LST array in degrees Celsius.

        window_size:
            Odd-sized local neighbourhood.

        z_threshold:
            Local Z-score threshold.

        Returns
        -------
        local_z:
            Float32 local Z-score raster.

        anomaly_mask:
            Boolean local-hot-anomaly raster.

        statistics:
            Dictionary containing local-analysis parameters.
        """

        if window_size is None:
            window_size = ThermalConfig.LOCAL_WINDOW_SIZE

        if z_threshold is None:
            z_threshold = ThermalConfig.LOCAL_Z_THRESHOLD

        if window_size < 3:
            raise ValueError(
                "LOCAL_WINDOW_SIZE must be at least 3."
            )

        if window_size % 2 == 0:
            raise ValueError(
                "LOCAL_WINDOW_SIZE must be odd."
            )

        lst = np.asarray(
            lst_celsius,
            dtype=np.float32,
        )

        self._validate_array(lst)

        valid_mask = self._valid_mask(lst)

        # -----------------------------------------------------
        # Replace invalid pixels with zero temporarily.
        # Their contribution is removed using valid-pixel count.
        # -----------------------------------------------------

        values = np.where(
            valid_mask,
            lst,
            0.0,
        ).astype(np.float32)

        valid_float = valid_mask.astype(
            np.float32
        )

        # -----------------------------------------------------
        # Local valid-pixel count
        # -----------------------------------------------------

        local_count = ndimage.uniform_filter(
            valid_float,
            size=window_size,
            mode="constant",
            cval=0.0,
        )

        window_area = float(
            window_size * window_size
        )

        local_count = (
            local_count * window_area
        )

        # -----------------------------------------------------
        # Local sum
        # -----------------------------------------------------

        local_sum = (
            ndimage.uniform_filter(
                values,
                size=window_size,
                mode="constant",
                cval=0.0,
            )
            * window_area
        )

        # -----------------------------------------------------
        # Local mean
        # -----------------------------------------------------

        local_mean = np.zeros_like(
            lst,
            dtype=np.float32,
        )

        has_neighbours = (
            local_count > 0
        )

        local_mean[has_neighbours] = (
            local_sum[has_neighbours]
            / local_count[has_neighbours]
        )

        # -----------------------------------------------------
        # Local squared mean
        # -----------------------------------------------------

        squared_values = np.where(
            valid_mask,
            lst ** 2,
            0.0,
        ).astype(np.float32)

        local_sq_sum = (
            ndimage.uniform_filter(
                squared_values,
                size=window_size,
                mode="constant",
                cval=0.0,
            )
            * window_area
        )

        local_sq_mean = np.zeros_like(
            lst,
            dtype=np.float32,
        )

        local_sq_mean[has_neighbours] = (
            local_sq_sum[has_neighbours]
            / local_count[has_neighbours]
        )

        # -----------------------------------------------------
        # Local variance
        # -----------------------------------------------------

        local_variance = (
            local_sq_mean
            - local_mean ** 2
        )

        # Numerical precision can produce tiny negative values.
        local_variance = np.maximum(
            local_variance,
            0.0,
        )

        local_std = np.sqrt(
            local_variance
        )

        # -----------------------------------------------------
        # Local Z-score
        # -----------------------------------------------------

        local_z = np.full(
            lst.shape,
            ThermalConfig.OUTPUT_NODATA,
            dtype=np.float32,
        )

        valid_for_z = (
            valid_mask
            & has_neighbours
            & (local_std > 1e-10)
        )

        local_z[valid_for_z] = (
            (
                lst[valid_for_z]
                - local_mean[valid_for_z]
            )
            / local_std[valid_for_z]
        ).astype(np.float32)

        # -----------------------------------------------------
        # Local hot anomaly
        # -----------------------------------------------------

        anomaly_mask = (
            valid_for_z
            & (local_z >= z_threshold)
        )

        logger.info(
            "Local anomaly detection: "
            "window=%dx%d, threshold=%.2fσ, "
            "anomaly_px=%d",
            window_size,
            window_size,
            z_threshold,
            int(np.sum(anomaly_mask)),
        )

        statistics = {
            "window_size": int(window_size),
            "threshold": float(z_threshold),
            "anomaly_pixels": int(
                np.sum(anomaly_mask)
            ),
        }

        return (
            local_z,
            anomaly_mask,
            statistics,
        )

    # =========================================================
    # HOTSPOT DETECTION
    # =========================================================

    def detect_hotspots(
        self,
        lst_celsius: np.ndarray,
        min_area_pixels: int = None,
        threshold_percentile: float = None,
    ):
        """
        Detect thermal hotspots using connected-component analysis.

        Method
        ------
        1. Calculate the selected percentile of valid LST.
        2. Select pixels >= percentile threshold.
        3. Apply 8-connected component analysis.
        4. Remove components smaller than the minimum size.
        5. Calculate statistics for retained components.

        Parameters
        ----------
        lst_celsius:
            2D LST array in degrees Celsius.

        min_area_pixels:
            Minimum number of pixels required for a hotspot.
            Defaults to ThermalConfig.HOTSPOT_MIN_PIXELS.

        threshold_percentile:
            Percentile used to define hotspots.
            Defaults to ThermalConfig.HOTSPOT_PERCENTILE.

        Returns
        -------
        hotspot_map:
            uint8 binary hotspot map.
            1 = retained hotspot
            0 = background.

        hotspot_info:
            List containing statistics for each retained hotspot.

        statistics:
            Overall hotspot statistics.
        """

        if min_area_pixels is None:
            min_area_pixels = (
                ThermalConfig.HOTSPOT_MIN_PIXELS
            )

        if threshold_percentile is None:
            threshold_percentile = (
                ThermalConfig.HOTSPOT_PERCENTILE
            )

        lst = np.asarray(
            lst_celsius,
            dtype=np.float32,
        )

        self._validate_array(lst)

        # -----------------------------------------------------
        # Valid pixels
        # -----------------------------------------------------

        valid_mask = self._valid_mask(lst)

        valid_values = lst[valid_mask]

        if valid_values.size == 0:

            logger.warning(
                "Hotspot detection: no valid pixels."
            )

            empty_map = np.zeros(
                lst.shape,
                dtype=np.uint8,
            )

            statistics = {
                "threshold_percentile": float(
                    threshold_percentile
                ),
                "threshold_c": None,
                "min_area_pixels": int(
                    min_area_pixels
                ),
                "initial_hot_pixels": 0,
                "initial_hot_area_km2": 0.0,
                "initial_components": 0,
                "final_hot_pixels": 0,
                "final_hot_area_km2": 0.0,
                "n_hotspots": 0,
            }

            return (
                empty_map,
                [],
                statistics,
            )

        # -----------------------------------------------------
        # Validate percentile
        # -----------------------------------------------------

        if not (
            0.0
            <= threshold_percentile
            <= 100.0
        ):
            raise ValueError(
                "threshold_percentile must be "
                "between 0 and 100."
            )

        if min_area_pixels < 1:
            raise ValueError(
                "min_area_pixels must be at least 1."
            )

        # -----------------------------------------------------
        # Percentile threshold
        # -----------------------------------------------------

        threshold = float(
            np.percentile(
                valid_values,
                threshold_percentile,
            )
        )

        logger.info(
            "Hotspot threshold: %.4f°C (P%.0f)",
            threshold,
            threshold_percentile,
        )

        # -----------------------------------------------------
        # Initial hot-pixel mask
        # -----------------------------------------------------

        hot_mask = (
            valid_mask
            & (lst >= threshold)
        )

        initial_hot_pixels = int(
            np.sum(hot_mask)
        )

        # -----------------------------------------------------
        # 8-connected component labeling
        # -----------------------------------------------------

        structure = np.ones(
            (3, 3),
            dtype=np.uint8,
        )

        labeled, n_features = ndimage.label(
            hot_mask,
            structure=structure,
        )

        logger.info(
            "Initial hotspot components: %d",
            n_features,
        )

        # -----------------------------------------------------
        # Efficient component-size calculation
        #
        # np.bincount() is much faster than repeatedly
        # evaluating:
        #
        #     labeled == component_id
        #
        # across the complete raster.
        # -----------------------------------------------------

        component_sizes = np.bincount(
            labeled.ravel()
        )

        # Component 0 is background.
        retained_ids = np.where(
            component_sizes >= min_area_pixels
        )[0]

        retained_ids = retained_ids[
            retained_ids != 0
        ]

        # -----------------------------------------------------
        # Create final binary hotspot map
        # -----------------------------------------------------

        hotspot_map = np.isin(
            labeled,
            retained_ids,
        ).astype(np.uint8)

        final_hot_pixels = int(
            np.sum(hotspot_map)
        )

        # -----------------------------------------------------
        # Component information
        #
        # find_objects() gives bounding boxes for components.
        # Therefore, we process only the small region occupied
        # by each retained component rather than the entire
        # raster.
        # -----------------------------------------------------

        hotspot_info = []

        object_slices = ndimage.find_objects(
            labeled
        )

        scene_mean = float(
            np.mean(valid_values)
        )

        for component_id in retained_ids:

            component_id = int(
                component_id
            )

            index = (
                component_id - 1
            )

            if (
                index < 0
                or index >= len(object_slices)
            ):
                continue

            component_slice = (
                object_slices[index]
            )

            if component_slice is None:
                continue

            rows, cols = component_slice

            local_labels = labeled[
                rows,
                cols
            ]

            local_mask = (
                local_labels == component_id
            )

            local_temperature = (
                lst[
                    rows,
                    cols
                ][local_mask]
            )

            if local_temperature.size == 0:
                continue

            # -------------------------------------------------
            # Temperature statistics
            # -------------------------------------------------

            mean_temperature = float(
                np.mean(
                    local_temperature
                )
            )

            max_temperature = float(
                np.max(
                    local_temperature
                )
            )

            # -------------------------------------------------
            # Centroid
            # -------------------------------------------------

            local_centroid = (
                ndimage.center_of_mass(
                    local_mask
                )
            )

            centroid_row = int(
                rows.start
                + local_centroid[0]
            )

            centroid_col = int(
                cols.start
                + local_centroid[1]
            )

            # -------------------------------------------------
            # Area
            #
            # Current project:
            # 30 m Landsat thermal resolution.
            # -------------------------------------------------

            area_pixels = int(
                component_sizes[
                    component_id
                ]
            )

            area_m2 = (
                area_pixels
                * 30.0
                * 30.0
            )

            area_hectares = (
                area_m2 / 10_000.0
            )

            hotspot_info.append(
                {
                    "id": component_id,

                    "area_pixels": area_pixels,

                    "area_hectares": round(
                        area_hectares,
                        4,
                    ),

                    "mean_temp_c": round(
                        mean_temperature,
                        4,
                    ),

                    "max_temp_c": round(
                        max_temperature,
                        4,
                    ),

                    "anomaly_c": round(
                        mean_temperature
                        - scene_mean,
                        4,
                    ),

                    "centroid_row": (
                        centroid_row
                    ),

                    "centroid_col": (
                        centroid_col
                    ),
                }
            )

        # -----------------------------------------------------
        # Overall hotspot statistics
        # -----------------------------------------------------

        pixel_area_m2 = (
            30.0 * 30.0
        )

        hotspot_statistics = {

            "threshold_percentile": float(
                threshold_percentile
            ),

            "threshold_c": round(
                threshold,
                4,
            ),

            "min_area_pixels": int(
                min_area_pixels
            ),

            "min_area_hectares": round(
                min_area_pixels
                * pixel_area_m2
                / 10_000.0,
                4,
            ),

            "initial_hot_pixels": (
                initial_hot_pixels
            ),

            "initial_hot_area_km2": round(
                initial_hot_pixels
                * pixel_area_m2
                / 1_000_000.0,
                4,
            ),

            "initial_components": int(
                n_features
            ),

            "final_hot_pixels": (
                final_hot_pixels
            ),

            "final_hot_area_km2": round(
                final_hot_pixels
                * pixel_area_m2
                / 1_000_000.0,
                4,
            ),

            "n_hotspots": len(
                hotspot_info
            ),
        }

        logger.info(
            "Hotspots completed: "
            "%d retained components, "
            "%d pixels, %.4f km²",
            len(hotspot_info),
            final_hot_pixels,
            hotspot_statistics[
                "final_hot_area_km2"
            ],
        )

        return (
            hotspot_map,
            hotspot_info,
            hotspot_statistics,
        )

    # =========================================================
    # HELPER: ARRAY VALIDATION
    # =========================================================

    @staticmethod
    def _validate_array(
        array: np.ndarray,
    ) -> None:
        """
        Validate a thermal raster.
        """

        if array.ndim != 2:

            raise ValueError(
                "LST raster must be a 2D array. "
                f"Received shape: {array.shape}"
            )

        if array.size == 0:

            raise ValueError(
                "LST raster is empty."
            )

    # =========================================================
    # HELPER: VALID PIXEL MASK
    # =========================================================

    @staticmethod
    def _valid_mask(
        lst_celsius: np.ndarray,
    ) -> np.ndarray:
        """
        Return valid-pixel mask.

        Current project convention:
            0 = NoData

        Invalid values:
            NaN
            +infinity
            -infinity
            0
        """

        valid = np.isfinite(
            lst_celsius
        )

        valid &= (
            lst_celsius
            != ThermalConfig.INPUT_NODATA
        )

        return valid

    # =========================================================
    # HELPER: EMPTY FLOAT OUTPUT
    # =========================================================

    @staticmethod
    def _empty_float_output(
        shape,
    ) -> np.ndarray:
        """
        Create an empty float32 raster using the configured
        output NoData value.
        """

        return np.full(
            shape,
            ThermalConfig.OUTPUT_NODATA,
            dtype=np.float32,
        )