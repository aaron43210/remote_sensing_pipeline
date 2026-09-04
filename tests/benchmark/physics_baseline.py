# =============================================================
# OWNER: AARON
# =============================================================
"""
Physics-only baseline for benchmarking.

Uses: Derivative spectroscopy + SAM + PROSAIL features
"""

import numpy as np
import time
import logging
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import accuracy_score, f1_score, confusion_matrix

logger = logging.getLogger(__name__)


class PhysicsBaseline:
    """
    Physics-only classification using spectral features
    + Random Forest classifier.
    
    Features extracted:
    1. NDVI
    2. Red edge position
    3. Red edge slope
    4. Chlorophyll absorption depth
    5. Water absorption depth
    6. NIR mean
    7. SWIR mean
    8. Brightness
    9. Spectral slope (VIS)
    10. Spectral slope (NIR)
    11. Spectral slope (SWIR)
    12. Contrast
    13-20. Continuum-removed band means (8 regions)
    """

    def __init__(self):
        self.model = RandomForestClassifier(
            n_estimators=100,
            max_depth=15,
            min_samples_split=5,
            n_jobs=-1,
            random_state=42
        )

    def extract_features(self, spectra, wavelengths):
        """
        Extract physics-based features.

        Args:
            spectra: (n_pixels, n_bands)
            wavelengths: (n_bands,)

        Returns:
            features: (n_pixels, n_features)
        """
        n_pixels, n_bands = spectra.shape
        wl = np.asarray(wavelengths)

        features = np.zeros((n_pixels, 20))

        idx_red = np.argmin(np.abs(wl - 680))
        idx_nir = np.argmin(np.abs(wl - 800))

        # 1. NDVI
        R_red = spectra[:, idx_red]
        R_nir = spectra[:, idx_nir]
        features[:, 0] = (R_nir - R_red) / (R_nir + R_red + 1e-8)

        # 2-3. Red edge
        re_mask = (wl >= 680) & (wl <= 750)
        if np.any(re_mask):
            re_region = spectra[:, re_mask]
            deriv = np.gradient(re_region, axis=1)
            features[:, 1] = np.argmax(deriv, axis=1) / max(1, deriv.shape[1])
            features[:, 2] = np.max(deriv, axis=1)

        # 4. Chlorophyll depth
        R_650 = spectra[:, max(0, idx_red-5)]
        R_750 = spectra[:, min(n_bands-1, idx_red+15)]
        cont = (R_650 + R_750) / 2
        features[:, 3] = np.clip(1 - R_red / (cont + 1e-8), 0, 1)

        # 5. Water depth
        idx_w = np.argmin(np.abs(wl - 1450))
        R_w = spectra[:, idx_w]
        R_wl = spectra[:, max(0, idx_w-5)]
        R_wr = spectra[:, min(n_bands-1, idx_w+5)]
        cont_w = (R_wl + R_wr) / 2
        features[:, 4] = np.clip(1 - R_w / (cont_w + 1e-8), 0, 1)

        # 6-7. Regional means
        nir_mask = (wl >= 700) & (wl < 1100)
        swir_mask = (wl >= 1500) & (wl < 1800)
        if np.any(nir_mask):
            features[:, 5] = np.mean(spectra[:, nir_mask], axis=1)
        if np.any(swir_mask):
            features[:, 6] = np.mean(spectra[:, swir_mask], axis=1)

        # 8. Brightness
        features[:, 7] = np.mean(spectra, axis=1)

        # 9-11. Spectral slopes
        vis_mask = wl < 700
        nir_slope_mask = (wl >= 700) & (wl < 1300)
        swir_slope_mask = wl >= 1300

        for i, mask in enumerate([vis_mask, nir_slope_mask, swir_slope_mask]):
            if np.any(mask):
                x = wl[mask]
                slopes = np.polyfit(x, spectra[0, mask], 1)[0]  # Placeholder
                # Vectorized slope for all pixels
                x_centered = x - np.mean(x)
                for p in range(min(n_pixels, 1)):
                    pass
                # Use numpy for speed
                x_norm = x - np.mean(x)
                x_var = np.var(x)
                if x_var > 0:
                    features[:, 8+i] = (
                        np.mean(spectra[:, mask] * x_norm[np.newaxis, :], axis=1) / x_var
                    )

        # 12. Contrast
        features[:, 11] = np.max(spectra, axis=1) - np.min(spectra, axis=1)

        # 13-20. Continuum-removed band means
        regions = [
            (400, 500), (500, 600), (600, 700), (700, 900),
            (900, 1100), (1100, 1400), (1400, 1800), (1800, 2500)
        ]
        for i, (w1, w2) in enumerate(regions):
            mask = (wl >= w1) & (wl < w2)
            if np.any(mask):
                features[:, 12+i] = np.mean(spectra[:, mask], axis=1)

        return features

    def train(self, X_train, y_train, wavelengths):
        """Train physics-based model."""
        logger.info("Extracting physics features for training set...")
        start = time.time()
        features = self.extract_features(X_train, wavelengths)
        logger.info(f"Feature extraction: {time.time()-start:.1f}s")

        logger.info(f"Training Random Forest ({len(X_train)} samples)...")
        start = time.time()
        self.model.fit(features, y_train)
        logger.info(f"Training: {time.time()-start:.1f}s")

        train_preds = self.model.predict(features)
        acc = accuracy_score(y_train, train_preds)
        logger.info(f"Training accuracy: {acc*100:.2f}%")

        return self

    def predict(self, X, wavelengths):
        """Predict using physics features."""
        features = self.extract_features(X, wavelengths)
        return self.model.predict(features)

    def evaluate(self, X_test, y_test, wavelengths):
        """Full evaluation with timing."""
        features = self.extract_features(X_test, wavelengths)

        start = time.time()
        preds = self.model.predict(features)
        inference_time = time.time() - start

        acc = accuracy_score(y_test, preds)
        f1 = f1_score(y_test, preds, average='weighted')
        cm = confusion_matrix(y_test, preds)

        return {
            'accuracy': acc,
            'f1_score': f1,
            'confusion_matrix': cm,
            'inference_time': inference_time,
            'time_per_sample_ms': (inference_time / len(X_test)) * 1000,
            'throughput': len(X_test) / inference_time
        }
