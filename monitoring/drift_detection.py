# =============================================================
# OWNER: AARON
# =============================================================
import numpy as np
import logging
from scipy import stats
import redis
import json
import os

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class DriftDetector:
    """
    Detects model drift in production hyperspectral predictions.

    Monitors:
    1. Data drift (input spectrum distribution shift)
    2. Prediction drift (output distribution shift)
    3. Physics consistency drift (PROSAIL reconstruction error)
    """

    def __init__(self):
        self.redis = redis.Redis(
            host=os.getenv("REDIS_HOST", "redis"),
            port=6379,
            decode_responses=True
        )
        self.drift_threshold = 0.05  # KS test p-value threshold

    def log_prediction(self, scene_id, spectra, predictions, params):
        """Log prediction for drift monitoring."""
        stats_data = {
            "scene_id": scene_id,
            "spectrum_mean": float(np.mean(spectra)),
            "spectrum_std": float(np.std(spectra)),
            "ndvi_mean": float(np.mean(predictions)),
            "chlorophyll_mean": float(np.mean(params[:, 0])),
            "lai_mean": float(np.mean(params[:, 2])),
            "timestamp": str(np.datetime64("now"))
        }

        # Store in Redis list (keep last 1000)
        self.redis.lpush("prediction_stats", json.dumps(stats_data))
        self.redis.ltrim("prediction_stats", 0, 999)

    def check_production_drift(self):
        """
        Compare recent predictions vs reference distribution.
        Uses Kolmogorov-Smirnov test.
        """
        # Load recent predictions
        recent_raw = self.redis.lrange("prediction_stats", 0, 99)
        recent = [json.loads(r) for r in recent_raw]

        if len(recent) < 20:
            return {
                "drift_detected": False,
                "reason": "Not enough data",
                "drift_score": 0.0
            }

        # Load reference distribution
        reference_raw = self.redis.lrange("prediction_stats", 100, 499)
        reference = [json.loads(r) for r in reference_raw]

        if len(reference) < 20:
            return {
                "drift_detected": False,
                "reason": "No reference data yet",
                "drift_score": 0.0
            }

        # KS test on spectrum statistics
        recent_means = [r["spectrum_mean"] for r in recent]
        reference_means = [r["spectrum_mean"] for r in reference]

        ks_stat, p_value = stats.ks_2samp(recent_means, reference_means)

        drift_detected = p_value < self.drift_threshold

        if drift_detected:
            logger.warning(
                f"Data drift detected: KS={ks_stat:.4f}, p={p_value:.4f}"
            )
        else:
            logger.info(
                f"No drift: KS={ks_stat:.4f}, p={p_value:.4f}"
            )

        return {
            "drift_detected": drift_detected,
            "drift_score": float(ks_stat),
            "p_value": float(p_value),
            "threshold": self.drift_threshold,
            "recent_samples": len(recent),
            "reference_samples": len(reference)
        }

    def check_physics_consistency(self, observed_spectra, predicted_params):
        """
        Check if predicted biophysical parameters are
        physically consistent with observed spectra.
        """
        from services.ml.lightweight_hybrid import PhysicsConsistencyLoss
        import torch

        loss_fn = PhysicsConsistencyLoss()

        spectra_tensor = torch.FloatTensor(observed_spectra)
        params_tensor = torch.FloatTensor(predicted_params)

        with torch.no_grad():
            simulated = loss_fn.prosail_simple(params_tensor, observed_spectra.shape[1])
            reconstruction_error = torch.mean(
                (simulated - spectra_tensor) ** 2
            ).item()

        is_consistent = reconstruction_error < 0.01

        return {
            "is_consistent": is_consistent,
            "reconstruction_error": reconstruction_error,
            "threshold": 0.01
        }
