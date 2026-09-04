# =============================================================
# OWNER: AARON
# =============================================================
import pytest
import torch
import numpy as np
from services.ml.lightweight_hybrid import (
    PhysicsGuidedLightweightNet,
    PhysicsConsistencyLoss
)


class TestLightweightModel:

    def setup_method(self):
        self.n_bands = 200
        self.n_classes = 16
        self.batch_size = 8
        self.model = PhysicsGuidedLightweightNet(
            n_bands=self.n_bands,
            n_classes=self.n_classes,
            embedding_dim=32
        )
        self.spectra = torch.rand(self.batch_size, self.n_bands)

    def test_output_shape_classification(self):
        logits, params = self.model(self.spectra)
        assert logits.shape == (self.batch_size, self.n_classes)

    def test_output_shape_params(self):
        logits, params = self.model(self.spectra)
        assert params.shape == (self.batch_size, 4)

    def test_model_parameter_count(self):
        total = sum(p.numel() for p in self.model.parameters() if p.requires_grad)
        # Should be well under 100K parameters
        assert total < 100_000, f"Too many parameters: {total}"

    def test_no_nan_in_output(self):
        logits, params = self.model(self.spectra)
        assert not torch.any(torch.isnan(logits))
        assert not torch.any(torch.isnan(params))

    def test_cpu_inference_time(self):
        import time
        n_pixels = 1000
        spectra = torch.rand(n_pixels, self.n_bands)

        start = time.time()
        with torch.no_grad():
            for i in range(0, n_pixels, 32):
                batch = spectra[i:i+32]
                self.model(batch)
        elapsed = time.time() - start

        ms_per_pixel = (elapsed / n_pixels) * 1000
        # Should be under 5ms per pixel on CPU
        assert ms_per_pixel < 5.0, f"Too slow: {ms_per_pixel:.2f}ms/pixel"


class TestPhysicsConsistencyLoss:

    def setup_method(self):
        self.loss_fn = PhysicsConsistencyLoss(lambda_physics=0.1)
        self.batch_size = 8
        self.n_bands = 200
        self.n_classes = 16

    def test_loss_is_positive(self):
        logits = torch.randn(self.batch_size, self.n_classes)
        params = torch.rand(self.batch_size, 4)
        targets = torch.randint(0, self.n_classes, (self.batch_size,))
        spectra = torch.rand(self.batch_size, self.n_bands)

        loss, loss_dict = self.loss_fn(logits, params, targets, spectra)
        assert loss.item() > 0

    def test_physics_loss_present(self):
        logits = torch.randn(self.batch_size, self.n_classes)
        params = torch.rand(self.batch_size, 4)
        targets = torch.randint(0, self.n_classes, (self.batch_size,))
        spectra = torch.rand(self.batch_size, self.n_bands)

        loss, loss_dict = self.loss_fn(logits, params, targets, spectra)
        assert "physics" in loss_dict
        assert loss_dict["physics"] > 0
