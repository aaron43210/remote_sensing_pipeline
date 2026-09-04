# =============================================================
# OWNER: AARON
# =============================================================
import pytest
import numpy as np
import torch
from services.spectral_analysis.derivative_analysis import (
    first_derivative, red_edge_position,
    continuum_removal, absorption_depth
)
from services.ml.lightweight_hybrid import PhysicsFeatureExtractor


class TestDerivativeAnalysis:

    def setup_method(self):
        self.wavelengths = np.linspace(400, 2500, 200)
        # Simulated vegetation spectrum
        self.spectrum = 0.3 + 0.2 * np.sin((self.wavelengths - 700) / 100)
        self.spectrum += np.random.randn(200) * 0.01

    def test_first_derivative_shape(self):
        deriv = first_derivative(self.spectrum, self.wavelengths)
        assert deriv.shape == self.spectrum.shape

    def test_first_derivative_not_all_zero(self):
        deriv = first_derivative(self.spectrum, self.wavelengths)
        assert not np.all(deriv == 0)

    def test_red_edge_position_in_range(self):
        pos, slope = red_edge_position(self.spectrum, self.wavelengths)
        assert 680 <= pos <= 750, f"REP {pos} outside valid range"

    def test_absorption_depth_non_negative(self):
        depth = absorption_depth(self.spectrum, self.wavelengths)
        assert depth >= 0.0

    def test_absorption_depth_valid_range(self):
        depth = absorption_depth(self.spectrum, self.wavelengths)
        assert 0.0 <= depth <= 1.0


class TestPhysicsFeatureExtractor:

    def setup_method(self):
        self.extractor = PhysicsFeatureExtractor()
        self.batch_size = 10
        self.n_bands = 200
        self.spectra = torch.rand(self.batch_size, self.n_bands)

    def test_output_shape(self):
        features = self.extractor(self.spectra)
        assert features.shape == (self.batch_size, 8)

    def test_ndvi_range(self):
        features = self.extractor(self.spectra)
        ndvi = features[:, 0]
        assert torch.all(ndvi >= -1.0) and torch.all(ndvi <= 1.0)

    def test_brightness_positive(self):
        features = self.extractor(self.spectra)
        brightness = features[:, 7]
        assert torch.all(brightness >= 0)

    def test_no_nan_output(self):
        features = self.extractor(self.spectra)
        assert not torch.any(torch.isnan(features))

    def test_batch_independence(self):
        single = self.extractor(self.spectra[0:1])
        batch = self.extractor(self.spectra)
        assert torch.allclose(single[0], batch[0], atol=1e-5)
