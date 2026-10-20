# =============================================================
# OWNER: BAINTY KAUR
# =============================================================
"""
Thermal Anomaly Detection

Detects thermal anomalies using physics-based and statistical methods.
No CNN or deep learning — pure physics, statistics, and signal processing.

Detectable anomalies:
    - Urban Heat Islands (UHI)
    - Industrial heat sources
    - Wildfire hotspots
    - Geothermal features
    - Agricultural stress (irrigation failures)

Methods implemented:
    1. Global z-score anomaly
    2. Local moving-window anomaly
    3. Urban Heat Island (UHI) intensity
    4. Hotspot detection (connected component labeling)

References
----------
- Voogt & Oke (2003): "Thermal remote sensing of urban climates."
  Remote Sensing of Environment, 86, 370-384.
- Li et al. (2020): "Urban heat island detection and analysis."
  ISPRS Journal of Photogrammetry and Remote Sensing, 164, 45-56.
- Wan et al. (2004): "Quality assessment and validation of the
  MODIS global land surface temperature." Int. J. Remote Sensing.
"""

import numpy as np
from scipy import ndimage
import logging

logger = logging.getLogger(__name__)


class ThermalAnomalyDetector:
    """
    Physics-based thermal anomaly detection for Landsat LST data.

    Usage:
        detector = ThermalAnomalyDetector()
        z_scores, hot, cold = detector.detect_global_anomalies(lst)
        local_z, anomalies  = detector.detect_local_anomalies(lst)
        uhi, details        = detector.compute_uhi_intensity(lst)
        hotspot_map, info   = detector.detect_hotspots(lst)
    """

    def detect_global_anomalies(self, lst_celsius: np.ndarray,
                                 z_threshold: float = 2.0):
        """
        Scene-wide z-score anomaly detection.

        Each pixel is scored relative to the whole scene mean and std.
        Pixels > z_threshold standard deviations from mean = anomaly.

        Args:
            lst_celsius:  (rows, cols) LST in °C.
            z_threshold:  Number of σ for anomaly (default 2.0 → ~95th percentile).

        Returns:
            z_scores:  (rows, cols) float32  z-score per pixel.
            hot_mask:  (rows, cols) bool     True = hot anomaly.
            cold_mask: (rows, cols) bool     True = cold anomaly.
        """
        valid = lst_celsius[(~np.isnan(lst_celsius)) & (lst_celsius != 0)]

        if len(valid) == 0:
            z = np.zeros_like(lst_celsius, dtype=np.float32)
            return z, z.astype(bool), z.astype(bool)

        mean = float(np.mean(valid))
        std  = float(np.std(valid))

        if std < 0.01:
            z = np.zeros_like(lst_celsius, dtype=np.float32)
            return z, z.astype(bool), z.astype(bool)

        z_scores = ((lst_celsius - mean) / std).astype(np.float32)
        z_scores = np.nan_to_num(z_scores, nan=0.0)

        hot_mask  = z_scores >  z_threshold
        cold_mask = z_scores < -z_threshold

        logger.info(
            "Global anomalies: mean=%.1f°C, std=%.1f°C, "
            "hot_px=%d, cold_px=%d (threshold=±%.1fσ)",
            mean, std, int(np.sum(hot_mask)), int(np.sum(cold_mask)), z_threshold
        )

        return z_scores, hot_mask, cold_mask

    def detect_local_anomalies(self, lst_celsius: np.ndarray,
                                window_size: int = 11,
                                z_threshold: float = 2.0):
        """
        Local neighbourhood z-score anomaly detection.

        Each pixel is compared to its local spatial neighbourhood.
        Better than global for detecting small-scale hotspots embedded
        in warm backgrounds (e.g. factory in a warm city).

        Args:
            lst_celsius:  (rows, cols) LST in °C.
            window_size:  Size of local neighbourhood in pixels (default 11 × 11).
            z_threshold:  Anomaly threshold.

        Returns:
            local_z:   (rows, cols) float32  local z-score.
            anomalies: (rows, cols) bool     True = local hot anomaly.
        """
        # Local mean via uniform filter
        local_mean = ndimage.uniform_filter(lst_celsius, size=window_size)

        # Local variance: E[X²] - E[X]²
        local_sq_mean = ndimage.uniform_filter(lst_celsius ** 2, size=window_size)
        local_var = np.maximum(local_sq_mean - local_mean ** 2, 0.0)
        local_std = np.sqrt(local_var)

        local_z = np.where(
            local_std > 0.01,
            (lst_celsius - local_mean) / local_std,
            0.0,
        ).astype(np.float32)

        local_z   = np.nan_to_num(local_z, nan=0.0)
        anomalies = local_z > z_threshold

        logger.info(
            "Local anomalies (window=%d): anomaly_px=%d",
            window_size, int(np.sum(anomalies))
        )

        return local_z, anomalies

    def compute_uhi_intensity(self, lst_celsius: np.ndarray,
                               urban_mask: np.ndarray = None,
                               rural_mask: np.ndarray = None):
        """
        Compute Urban Heat Island (UHI) intensity.

        UHI = T_urban_mean - T_rural_mean

        If masks are not provided, urban = top 20th percentile of temperature,
        rural = bottom 20th percentile. This is the standard approach when
        no land-cover map is available.

        Args:
            lst_celsius:  (rows, cols) LST in °C.
            urban_mask:   (rows, cols) bool  True = urban pixel. Optional.
            rural_mask:   (rows, cols) bool  True = rural pixel. Optional.

        Returns:
            uhi_intensity: float  UHI magnitude in °C.
            details:       dict   Component temperatures and classification.
        """
        valid = lst_celsius[(~np.isnan(lst_celsius)) & (lst_celsius != 0)]

        if len(valid) < 10:
            return 0.0, {'error': 'Insufficient valid pixels'}

        if urban_mask is None or rural_mask is None:
            p80 = float(np.percentile(valid, 80))
            p20 = float(np.percentile(valid, 20))
            urban_mask = lst_celsius > p80
            rural_mask = lst_celsius < p20

        urban_t = lst_celsius[urban_mask & (lst_celsius != 0) & ~np.isnan(lst_celsius)]
        rural_t = lst_celsius[rural_mask & (lst_celsius != 0) & ~np.isnan(lst_celsius)]

        if len(urban_t) == 0 or len(rural_t) == 0:
            return 0.0, {'error': 'Could not separate urban/rural areas'}

        t_urban = float(np.mean(urban_t))
        t_rural = float(np.mean(rural_t))
        uhi = t_urban - t_rural

        # Classify UHI severity
        if   uhi > 5.0: classification = 'EXTREME'
        elif uhi > 3.0: classification = 'STRONG'
        elif uhi > 1.0: classification = 'MODERATE'
        elif uhi > 0.0: classification = 'WEAK'
        else:           classification = 'NONE'

        details = {
            'uhi_intensity_c':  round(uhi, 2),
            'urban_mean_c':     round(t_urban, 2),
            'rural_mean_c':     round(t_rural, 2),
            'urban_pixels':     int(np.sum(urban_mask)),
            'rural_pixels':     int(np.sum(rural_mask)),
            'classification':   classification,
        }

        logger.info(
            "UHI: intensity=%.1f°C (%s), urban=%.1f°C, rural=%.1f°C",
            uhi, classification, t_urban, t_rural
        )

        return uhi, details

    def detect_hotspots(self, lst_celsius: np.ndarray,
                         min_area_pixels: int = 10,
                         threshold_percentile: float = 95.0):
        """
        Detect thermal hotspots using connected component analysis.

        Thresholds the temperature map at a high percentile, then labels
        contiguous hot regions. Each labelled region is a hotspot.

        Args:
            lst_celsius:          (rows, cols) LST in °C.
            min_area_pixels:      Minimum hotspot size in pixels (removes noise).
            threshold_percentile: Temperature percentile used as hotspot threshold.

        Returns:
            hotspot_map:  (rows, cols) int  Labelled hotspot regions (0 = background).
            hotspot_info: list of dicts describing each hotspot.
        """
        valid = lst_celsius[(~np.isnan(lst_celsius)) & (lst_celsius != 0)]

        if len(valid) == 0:
            return np.zeros_like(lst_celsius, dtype=np.int32), []

        threshold = float(np.percentile(valid, threshold_percentile))
        hot_mask  = lst_celsius > threshold

        # Label connected regions of hot pixels
        labeled, n_features = ndimage.label(hot_mask)

        scene_mean = float(np.mean(valid))
        hotspot_info = []

        for i in range(1, n_features + 1):
            region = labeled == i
            area   = int(np.sum(region))

            if area < min_area_pixels:
                labeled[region] = 0   # Discard tiny blobs (noise)
                continue

            region_t = lst_celsius[region]
            region_t = region_t[(region_t != 0) & ~np.isnan(region_t)]

            if len(region_t) == 0:
                continue

            centroid = ndimage.center_of_mass(region.astype(float))

            hotspot_info.append({
                'id':            i,
                'area_pixels':   area,
                'mean_temp_c':   round(float(np.mean(region_t)), 2),
                'max_temp_c':    round(float(np.max(region_t)), 2),
                'anomaly_c':     round(float(np.mean(region_t)) - scene_mean, 2),
                'centroid_row':  int(centroid[0]),
                'centroid_col':  int(centroid[1]),
            })

        logger.info(
            "Hotspots: %d detected above %.1f°C (p%.0f)",
            len(hotspot_info), threshold, threshold_percentile
        )

        return labeled.astype(np.int32), hotspot_info
