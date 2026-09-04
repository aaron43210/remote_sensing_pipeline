# =============================================================
# OWNER: AARON
# =============================================================
"""Shared test fixtures."""

import pytest
import numpy as np


@pytest.fixture
def sample_spectrum():
    """Generate a synthetic vegetation spectrum."""
    wavelengths = np.linspace(400, 2500, 200)
    spectrum = np.zeros(200)
    spectrum[wavelengths < 700] = 0.05
    spectrum[(wavelengths >= 700) & (wavelengths < 1300)] = 0.45
    spectrum[wavelengths >= 1300] = 0.25
    spectrum += np.random.normal(0, 0.01, 200)
    return wavelengths, np.clip(spectrum, 0, 1)


@pytest.fixture
def sample_hsi_cube():
    """Generate a small synthetic hyperspectral cube."""
    cube = np.random.rand(32, 32, 200).astype(np.float32) * 0.5 + 0.2
    wavelengths = np.linspace(400, 2500, 200)
    return cube, wavelengths


@pytest.fixture
def api_url():
    return "http://localhost:8000"
