# =============================================================
# OWNER: AARON
# =============================================================
import pytest
import numpy as np


# ── LST Calculator ────────────────────────────────────────────────────────────

class TestLSTCalculator:
    def setup_method(self):
        import sys, os
        sys.path.insert(0, os.path.join(os.path.dirname(__file__),
                                        "../../services/thermal"))
        from lst_calculator import LSTCalculator
        self.calc = LSTCalculator()

    def test_kelvin_to_celsius_no_emissivity(self):
        """300 K = 26.85°C"""
        kelvin = np.array([[300.0, 310.0], [320.0, 330.0]], dtype=np.float32)
        lst = self.calc.lst_from_kelvin(kelvin)
        expected = np.array([[26.85, 36.85], [46.85, 56.85]], dtype=np.float32)
        np.testing.assert_allclose(lst, expected, atol=0.5)

    def test_lst_values_in_valid_range(self):
        kelvin = np.random.uniform(280, 330, (50, 50)).astype(np.float32)
        lst = self.calc.lst_from_kelvin(kelvin)
        valid = lst[~np.isnan(lst)]
        assert np.all(valid > -50), "LST below -50°C detected"
        assert np.all(valid < 70),  "LST above 70°C detected"

    def test_emissivity_correction_lowers_temp(self):
        """Emissivity correction should yield a slightly lower temperature."""
        kelvin = np.array([[310.0]], dtype=np.float32)
        emissivity = np.array([[0.98]], dtype=np.float32)
        lst_with = self.calc.lst_from_kelvin(kelvin, emissivity)
        lst_without = self.calc.lst_from_kelvin(kelvin)
        assert lst_with[0, 0] < lst_without[0, 0]

    def test_dn_conversion(self):
        """DN 0 with default Landsat scale/offset → 149K = -124.15°C → masked as NaN."""
        dn = np.zeros((5, 5), dtype=np.float32)
        lst = self.calc.lst_from_dn(dn)
        # All values should be NaN (too cold, below -50°C)
        assert np.all(np.isnan(lst))

    def test_thermal_indices_returns_expected_keys(self):
        lst = np.random.uniform(25, 40, (30, 30)).astype(np.float32)
        result = self.calc.compute_thermal_indices(lst)
        for key in ("mean", "std", "min", "max", "thermal_anomaly", "classification"):
            assert key in result, f"Missing key: {key}"

    def test_thermal_classification_shape(self):
        lst = np.random.uniform(20, 50, (20, 20)).astype(np.float32)
        result = self.calc.compute_thermal_indices(lst)
        assert result["classification"].shape == (20, 20)


# ── Emissivity Calculator ─────────────────────────────────────────────────────

class TestEmissivityCalculator:
    def setup_method(self):
        import sys, os
        sys.path.insert(0, os.path.join(os.path.dirname(__file__),
                                        "../../services/thermal"))
        from emissivity import EmissivityCalculator
        self.emiss = EmissivityCalculator()

    def test_dense_vegetation_gives_high_emissivity(self):
        """NDVI = 0.8 (dense veg) → emissivity should be near ε_veg = 0.986"""
        ndvi = np.full((10, 10), 0.8, dtype=np.float32)
        e = self.emiss.estimate_from_ndvi(ndvi)
        assert np.all(e > 0.980), f"Expected >0.980, got min={e.min():.4f}"

    def test_bare_soil_gives_lower_emissivity(self):
        """NDVI = 0.05 (bare soil) → emissivity should be near ε_soil = 0.9625"""
        ndvi = np.full((10, 10), 0.05, dtype=np.float32)
        e = self.emiss.estimate_from_ndvi(ndvi)
        assert np.all(e < 0.965), f"Expected <0.965, got max={e.max():.4f}"

    def test_emissivity_always_in_valid_range(self):
        """Emissivity must always be in [0.90, 0.995]"""
        ndvi = np.random.uniform(-0.2, 0.9, (100, 100)).astype(np.float32)
        e = self.emiss.estimate_from_ndvi(ndvi)
        assert np.all(e >= 0.90),  f"Below 0.90: min={e.min():.4f}"
        assert np.all(e <= 0.995), f"Above 0.995: max={e.max():.4f}"

    def test_water_mask_detects_low_ndvi(self):
        ndvi = np.array([[-0.2, 0.3], [0.5, -0.15]], dtype=np.float32)
        mask = self.emiss.water_mask(ndvi, threshold=-0.1)
        assert mask[0, 0] is np.bool_(True)
        assert mask[0, 1] is np.bool_(False)


# ── Anomaly Detector ──────────────────────────────────────────────────────────

class TestThermalAnomalyDetector:
    def setup_method(self):
        import sys, os
        sys.path.insert(0, os.path.join(os.path.dirname(__file__),
                                        "../../services/thermal"))
        from anomaly_detector import ThermalAnomalyDetector
        self.detector = ThermalAnomalyDetector()

    def test_global_anomaly_detects_planted_hotspot(self):
        """Pixel set to extreme temperature should be flagged as hot anomaly."""
        lst = np.random.normal(30, 3, (100, 100)).astype(np.float32)
        lst[10, 10] = 65.0    # Extreme hotspot
        z, hot, cold = self.detector.detect_global_anomalies(lst, z_threshold=3.0)
        assert hot[10, 10], "Expected hotspot at (10,10) to be flagged"

    def test_global_anomaly_shape(self):
        lst = np.random.normal(25, 5, (50, 60)).astype(np.float32)
        z, hot, cold = self.detector.detect_global_anomalies(lst)
        assert z.shape    == (50, 60)
        assert hot.shape  == (50, 60)
        assert cold.shape == (50, 60)

    def test_local_anomaly_detects_small_hotspot(self):
        lst = np.full((100, 100), 30.0, dtype=np.float32)
        lst[50:53, 50:53] = 50.0   # Small hotspot against uniform background
        local_z, anomalies = self.detector.detect_local_anomalies(lst, window_size=11)
        # At least one of the hotspot pixels should be flagged
        assert np.any(anomalies[50:53, 50:53])

    def test_uhi_urban_area_hotter(self):
        """Simulated urban area (top rows) should yield positive UHI."""
        lst = np.random.normal(25, 2, (100, 100)).astype(np.float32)
        lst[:20, :] = 38.0   # Simulate hot urban area
        uhi, details = self.detector.compute_uhi_intensity(lst)
        assert uhi > 0, f"Expected positive UHI, got {uhi}"
        assert details["classification"] in ("WEAK", "MODERATE", "STRONG", "EXTREME")

    def test_uhi_classification_extreme(self):
        lst = np.random.normal(25, 2, (100, 100)).astype(np.float32)
        lst[:20, :] = 40.0   # Very hot urban band
        uhi, details = self.detector.compute_uhi_intensity(lst)
        assert uhi > 5.0
        assert details["classification"] == "EXTREME"

    def test_hotspot_detection_finds_planted_region(self):
        lst = np.random.normal(30, 3, (100, 100)).astype(np.float32)
        lst[40:51, 40:51] = 55.0   # 11×11 = 121 pixels hotspot
        labeled, info = self.detector.detect_hotspots(
            lst, min_area_pixels=10, threshold_percentile=95
        )
        assert len(info) >= 1, "Expected at least one hotspot to be detected"

    def test_hotspot_info_has_required_keys(self):
        lst = np.random.normal(30, 3, (100, 100)).astype(np.float32)
        lst[40:55, 40:55] = 55.0
        _, info = self.detector.detect_hotspots(lst, min_area_pixels=5)
        if info:
            required = ("id", "area_pixels", "mean_temp_c", "max_temp_c",
                        "anomaly_c", "centroid_row", "centroid_col")
            for key in required:
                assert key in info[0], f"Missing key in hotspot_info: {key}"

    def test_empty_input_returns_no_anomalies(self):
        lst = np.zeros((20, 20), dtype=np.float32)
        z, hot, cold = self.detector.detect_global_anomalies(lst)
        assert not np.any(hot)
        assert not np.any(cold)
