# =============================================================
# OWNER: ANANTHAN S & HARIKRISHNAN
# =============================================================
"""
Quality Assessment for Mineral Analysis
"""
import numpy as np
import logging

logger = logging.getLogger(__name__)

class QualityAssessor:
    def assess_pixel(self, sam_angle, reconstruction_error, confidence, feature_match):
        sam_quality = max(0, 1.0 - sam_angle / 0.3)
        unmix_quality = max(0, 1.0 - reconstruction_error / 0.1)
        feature_quality = feature_match.get("score", 0)
        combined = 0.3 * sam_quality + 0.3 * unmix_quality + 0.2 * confidence + 0.2 * feature_quality

        flags = []
        if sam_angle > 0.2: flags.append("HIGH_SAM_ANGLE")
        if reconstruction_error > 0.05: flags.append("HIGH_RECONSTRUCTION_ERROR")
        if not feature_match.get("validated", False): flags.append("FEATURE_VALIDATION_FAILED")

        return {
            "score": float(combined),
            "sam_quality": float(sam_quality),
            "unmixing_quality": float(unmix_quality),
            "feature_quality": float(feature_quality),
            "flags": flags
        }

    def assess_scene(self, angle_map, error_map, confidence_map):
        return {
            "mean_angle": float(np.nanmean(angle_map)),
            "mean_error": float(np.nanmean(error_map)),
            "mean_confidence": float(np.nanmean(confidence_map)),
            "high_confidence_fraction": float(np.nanmean(confidence_map > 0.8)),
            "high_error_fraction": float(np.nanmean(error_map > 0.05))
        }
