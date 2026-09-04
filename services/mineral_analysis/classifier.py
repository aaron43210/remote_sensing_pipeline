# =============================================================
# OWNER: ANANTHAN S & HARIKRISHNAN
# =============================================================
"""
Mineral Classification Pipeline
"""
import numpy as np
import logging
import time

from spectral_library import MineralSpectralLibrary
from wavelength_alignment import WavelengthAligner
from continuum_removal import ContinuumRemover
from absorption_features import AbsorptionFeatureExtractor
from sam import SpectralAngleMapper
from unmixing import LinearSpectralUnmixer
from quality import QualityAssessor
from config import DIAGNOSTIC_FEATURES

logger = logging.getLogger(__name__)

class MineralClassifier:
    def __init__(self, library_path=None):
        self.library = MineralSpectralLibrary(library_path)
        self.aligner = WavelengthAligner(method="cubic")
        self.continuum_remover = ContinuumRemover(method="hull")
        self.feature_extractor = AbsorptionFeatureExtractor()
        self.sam = SpectralAngleMapper()
        self.unmixer = LinearSpectralUnmixer(method="nnls")
        self.quality = QualityAssessor()
        self.aligned_library = None
        self.sensor_wavelengths = None
        self.library_features = {}

    def initialize_for_sensor(self, sensor_wavelengths):
        self.sensor_wavelengths = np.asarray(sensor_wavelengths, dtype=float)
        self.aligned_library = self.aligner.align_library(self.library, self.sensor_wavelengths)
        for name, spectrum in self.aligned_library.items():
            self.library_features[name] = self.feature_extractor.extract_features(self.sensor_wavelengths, spectrum)
        logger.info(f"Initialized for sensor: {len(self.sensor_wavelengths)} bands, {len(self.aligned_library)} minerals")

    def analyze_pixel(self, spectrum, wavelengths=None):
        start_time = time.time()
        wvl = wavelengths if wavelengths is not None else self.sensor_wavelengths
        spec = np.asarray(spectrum, dtype=float)

        if wvl is None: raise ValueError("Wavelengths not provided")
        if self.aligned_library is None or not np.array_equal(self.sensor_wavelengths, wvl):
            self.initialize_for_sensor(wvl)

        cr_spectrum, continuum = self.continuum_remover.remove(wvl, spec)
        pixel_features = self.feature_extractor.extract_features(wvl, spec, cr_spectrum)
        sam_result = self.sam.classify_pixel(spec, self.aligned_library, wvl)
        feature_match = self._validate_with_features(pixel_features, sam_result["mineral"])

        names = sorted(self.aligned_library.keys())
        matrix = np.array([self.aligned_library[n] for n in names]).T
        abundances, recon, error = self.unmixer.unmix_pixel(spec, matrix)

        quality = self.quality.assess_pixel(sam_result["angle"], error, sam_result["confidence"]["score"], feature_match)
        elapsed = time.time() - start_time

        return {
            "mineral": sam_result["mineral"],
            "sam_angle": sam_result["angle"],
            "confidence": sam_result["confidence"],
            "top_matches": sam_result["top_3"],
            "abundances": {n: float(abundances[i]) for i, n in enumerate(names)},
            "reconstruction_error": error,
            "features": {k: {"detected": v.get("detected"), "position": v.get("position"), "depth": v.get("depth")} 
                         for k, v in pixel_features.items() if isinstance(v, dict) and "detected" in v},
            "feature_validation": feature_match,
            "quality": quality,
            "processing_time_ms": elapsed * 1000
        }

    def analyze_scene(self, cube, wavelengths=None, subsample=1):
        start_time = time.time()
        rows, cols, bands = cube.shape
        wvl = wavelengths if wavelengths is not None else self.sensor_wavelengths

        if self.aligned_library is None:
            self.initialize_for_sensor(wvl)

        sam_result = self.sam.classify_scene(cube, self.aligned_library, wvl)
        
        names = sorted(self.aligned_library.keys())
        matrix = np.array([self.aligned_library[n] for n in names]).T
        abund_maps, error_map = self.unmixer.unmix_scene(cube, matrix, names)
        
        quality_map = self.quality.assess_scene(sam_result["angle_map"], error_map, sam_result["confidence_map"])
        elapsed = time.time() - start_time

        return {
            "classification": {
                "mineral_map": sam_result["class_name_map"],
                "class_indices": sam_result["class_indices"],
                "mineral_names": sam_result["mineral_names"].tolist()
            },
            "abundance": abund_maps,
            "confidence": sam_result["confidence_map"],
            "angle_map": sam_result["angle_map"],
            "reconstruction_error": error_map,
            "quality": quality_map,
            "processing_time_sec": elapsed,
            "pixels_processed": rows * cols
        }

    def _validate_with_features(self, pixel_features, predicted_mineral):
        if predicted_mineral not in self.library_features:
            return {"validated": False, "score": 0.0}
        
        mineral_features = self.library_features[predicted_mineral]
        match_scores = self.feature_extractor.match_diagnostic_features(pixel_features, mineral_features)
        valid_scores = [v for v in match_scores.values() if v > 0]
        avg_score = np.mean(valid_scores) if valid_scores else 0.0

        diag_matches = 0
        diag_total = 0
        for feat, config in DIAGNOSTIC_FEATURES.items():
            if predicted_mineral in config["minerals"]:
                diag_total += 1
                if match_scores.get(feat, 0) > 0.5: diag_matches += 1

        return {
            "validated": avg_score > 0.3,
            "score": float(avg_score),
            "diagnostic_matches": diag_matches,
            "diagnostic_total": diag_total,
            "feature_scores": {k: float(v) for k, v in match_scores.items()}
        }
