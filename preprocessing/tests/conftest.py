# =============================================================
# OWNER: ANANTAHANARAYANAN
# =============================================================
"""
Fixtures and import path for the preprocessing tests.

    cd preprocessing
    python -m pytest tests/ -v

No Kafka, MinIO or network needed: storage is a local folder.
"""

import os
import sys

import numpy as np
import pytest
import zarr

MODULE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, MODULE_DIR)     # config, pipeline, thermal/, ... as in /app


@pytest.fixture
def wavelengths():
    """285 bands, 381-2493 nm -- the EMIT axis."""
    return np.linspace(381.0, 2493.0, 285)


@pytest.fixture
def cube(wavelengths):
    """
    Small synthetic reflectance cube in 0..1, shaped like vegetation.

    Deliberately NOT white noise: real bands are smooth and correlated with
    their neighbours, which the SNR estimator and Savitzky-Golay rely on.
    Low visible, red edge near 700 nm, NIR plateau, falling SWIR, plus
    per-pixel brightness and sensor noise.
    """
    rng = np.random.default_rng(seed=42)

    anchors_wl = [381, 500, 680, 720, 800, 1300, 1600, 2200, 2493]
    anchors_r = [0.03, 0.05, 0.04, 0.30, 0.42, 0.40, 0.28, 0.18, 0.12]
    spectrum = np.interp(wavelengths, anchors_wl, anchors_r)

    brightness = rng.uniform(0.8, 1.2, size=(40, 40, 1))
    cube = spectrum[None, None, :] * brightness
    cube += rng.normal(0.0, 0.002, size=cube.shape)
    return np.clip(cube, 0.0, 1.0).astype(np.float32)


def write_like_ingestion(path, data, wavelengths, scene_id="EMIT_test"):
    """
    A Zarr laid out EXACTLY as ingestion/main.py writes raw.zarr: a plain
    array, chunks (64, 64, bands), wavelengths and scene_id in attrs.
    """
    store = zarr.open(str(path), mode="w", shape=data.shape, dtype="float32",
                      chunks=(64, 64, data.shape[2]))
    store[:] = data
    store.attrs["wavelengths"] = [float(w) for w in wavelengths]
    store.attrs["scene_id"] = scene_id
    return str(path)


@pytest.fixture
def raw_store(tmp_path, cube, wavelengths):
    """The vegetation cube, with one EMIT fill pixel, stored like ingestion."""
    data = cube.copy()
    data[0, 0, :] = -9999.0
    return write_like_ingestion(tmp_path / "raw.zarr", data, wavelengths)


@pytest.fixture
def source():
    """The message ingestion publishes, unchanged."""
    return {"scene_id": "EMIT_test", "satellite": "EMIT",
            "bbox": [-101.2, 45.0, -100.9, 45.3], "zarr_path": "EMIT_test/raw.zarr",
            "timestamp": "2026-10-03T00:00:00"}
