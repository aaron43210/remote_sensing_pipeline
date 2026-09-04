# =============================================================
# OWNER: ANANTHAN S & HARIKRISHNAN
# =============================================================
"""
Spectral Angle Mapper (SAM)
"""
import numpy as np
import logging

logger = logging.getLogger(__name__)

class SpectralAngleMapper:
    def __init__(self, thresholds=None):
        from config import SAM_THRESHOLDS
        self.thresholds = thresholds or SAM_THRESHOLDS

    def compute_angle(self, spectrum1, spectrum2):
        s1 = np.asarray(spectrum1, dtype=float)
        s2 = np.asarray(spectrum2, dtype=float)
        norm1, norm2 = np.linalg.norm(s1), np.linalg.norm(s2)
        if norm1 < 1e-10 or norm2 < 1e-10:
            return np.pi / 2
        cos_angle = np.clip(np.dot(s1, s2) / (norm1 * norm2), -1.0, 1.0)
        return float(np.arccos(cos_angle))

    def compute_angle_batch(self, spectra, library_matrix):
        spectra_norm = spectra / (np.linalg.norm(spectra, axis=1, keepdims=True) + 1e-10)
        lib_norm = library_matrix / (np.linalg.norm(library_matrix, axis=1, keepdims=True) + 1e-10)
        cos_angles = np.clip(np.dot(spectra_norm, lib_norm.T), -1.0, 1.0)
        return np.arccos(cos_angles)

    def classify_pixel(self, spectrum, library_dict, wavelengths=None):
        angles = {name: self.compute_angle(spectrum, spec) for name, spec in library_dict.items()}
        sorted_minerals = sorted(angles.items(), key=lambda x: x[1])
        best_mineral, best_angle = sorted_minerals[0]

        return {
            "mineral": best_mineral,
            "angle": best_angle,
            "confidence": self._assess_confidence(best_angle, sorted_minerals),
            "all_angles": dict(sorted_minerals),
            "top_3": [{"mineral": m, "angle": a} for m, a in sorted_minerals[:3]]
        }

    def classify_scene(self, cube, library_dict, wavelengths=None):
        rows, cols, bands = cube.shape
        names = sorted(library_dict.keys())
        matrix = np.array([library_dict[n] for n in names])
        
        pixels = cube.reshape(-1, bands)
        angles = self.compute_angle_batch(pixels, matrix)
        
        best_idx = np.argmin(angles, axis=1)
        best_angles = angles[np.arange(len(angles)), best_idx]
        confidence = np.clip(1.0 - (best_angles / (np.pi / 2)), 0, 1)

        name_map = np.array(names)
        return {
            "class_indices": best_idx.reshape(rows, cols),
            "mineral_names": name_map,
            "angle_map": best_angles.reshape(rows, cols),
            "confidence_map": confidence.reshape(rows, cols),
            "class_name_map": name_map[best_idx.reshape(rows, cols)]
        }

    def _assess_confidence(self, best_angle, sorted_minerals):
        absolute = 1.0 - (best_angle / (np.pi / 2))
        sep = (sorted_minerals[1][1] - best_angle) / (sorted_minerals[1][1] + 1e-10) if len(sorted_minerals) > 1 else 0.5
        combined = np.clip(0.6 * absolute + 0.4 * sep, 0, 1)

        if best_angle < self.thresholds["high_confidence"]: level = "HIGH"
        elif best_angle < self.thresholds["medium_confidence"]: level = "MEDIUM"
        elif best_angle < self.thresholds["low_confidence"]: level = "LOW"
        else: level = "REJECT"

        return {"score": float(combined), "level": level, "absolute": float(absolute), "separation": float(sep)}
