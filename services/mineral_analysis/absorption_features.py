# =============================================================
# OWNER: ANANTHAN S & HARIKRISHNAN
# =============================================================
"""
Absorption Feature Extraction for Mineral Identification
"""
import numpy as np
import logging

from continuum_removal import ContinuumRemover
from config import DIAGNOSTIC_FEATURES

logger = logging.getLogger(__name__)

class AbsorptionFeatureExtractor:
    def __init__(self, wavelengths=None):
        self.wavelengths = wavelengths
        self.continuum_remover = ContinuumRemover(method="hull")

    def extract_features(self, wavelengths, spectrum, cr_spectrum=None):
        wvl = np.asarray(wavelengths, dtype=float)
        spec = np.asarray(spectrum, dtype=float)

        if cr_spectrum is None:
            cr_spectrum, continuum = self.continuum_remover.remove(wvl, spec)

        features = {}
        for feature_name, feature_config in DIAGNOSTIC_FEATURES.items():
            center = feature_config["center"]
            shoulder_left = feature_config["shoulder_left"]
            shoulder_right = feature_config["shoulder_right"]

            feat = self._extract_single_feature(
                wvl, spec, cr_spectrum,
                center, shoulder_left, shoulder_right
            )
            features[feature_name] = feat

        features["overall"] = self._extract_overall_features(wvl, spec, cr_spectrum)
        return features

    def _extract_single_feature(self, wvl, spec, cr_spectrum, center, shoulder_left, shoulder_right):
        mask = (wvl >= shoulder_left) & (wvl <= shoulder_right)
        if not np.any(mask):
            return {"detected": False, "position": 0, "depth": 0, "width": 0, "area": 0, "asymmetry": 0}

        wvl_region = wvl[mask]
        cr_region = cr_spectrum[mask]

        min_idx = np.argmin(cr_region)
        position = wvl_region[min_idx]
        depth = max(0, 1.0 - cr_region[min_idx])
        width = self._compute_fwhm(wvl_region, cr_region)
        area = max(0, np.trapz(1.0 - cr_region, wvl_region))
        asymmetry = self._compute_asymmetry(wvl_region, cr_region, min_idx)
        detected = depth > 0.02

        return {
            "detected": bool(detected),
            "position": float(position),
            "depth": float(depth),
            "width": float(width),
            "area": float(area),
            "asymmetry": float(asymmetry)
        }

    def _compute_fwhm(self, wavelengths, cr_spectrum):
        min_cr = np.min(cr_spectrum)
        half_max = (1.0 + min_cr) / 2.0
        above = cr_spectrum >= half_max
        crossings = np.where(np.diff(above.astype(int)))[0]

        if len(crossings) >= 2:
            return max(0, wavelengths[crossings[-1]] - wavelengths[crossings[0]])
        return max(0, wavelengths[-1] - wavelengths[0])

    def _compute_asymmetry(self, wavelengths, cr_spectrum, min_idx):
        if min_idx == 0 or min_idx >= len(wavelengths) - 1:
            return 0.5
        left_area = np.trapz(1.0 - cr_spectrum[:min_idx + 1], wavelengths[:min_idx + 1])
        right_area = np.trapz(1.0 - cr_spectrum[min_idx:], wavelengths[min_idx:])
        total = left_area + right_area
        if total < 1e-10:
            return 0.5
        return float(left_area / total)

    def _extract_overall_features(self, wvl, spec, cr_spectrum):
        slope = np.polyfit(wvl, spec, 1)[0]
        significant = sum(
            1 for c in DIAGNOSTIC_FEATURES.values()
            if np.any((wvl >= c["shoulder_left"]) & (wvl <= c["shoulder_right"])) and
               (1.0 - np.min(cr_spectrum[(wvl >= c["shoulder_left"]) & (wvl <= c["shoulder_right"])])) > 0.05
        )

        return {
            "slope": float(slope),
            "brightness": float(np.mean(spec)),
            "variability": float(np.std(spec)),
            "cr_mean": float(np.mean(cr_spectrum)),
            "cr_std": float(np.std(cr_spectrum)),
            "n_significant_absorptions": significant
        }

    def match_diagnostic_features(self, pixel_features, mineral_features):
        scores = {}
        for feature_name in DIAGNOSTIC_FEATURES:
            pf = pixel_features.get(feature_name, {})
            mf = mineral_features.get(feature_name, {})
            if not pf.get("detected") or not mf.get("detected"):
                scores[feature_name] = 0.0
                continue
            pos_score = max(0, 1.0 - abs(pf["position"] - mf["position"]) / 20.0)
            depth_score = min(pf["depth"], mf["depth"]) / (max(pf["depth"], mf["depth"]) + 1e-10)
            scores[feature_name] = 0.6 * pos_score + 0.4 * depth_score
        return scores
