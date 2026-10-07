"""Leakage-safe thermal inference for selected-area ST_B10 rasters."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import rasterio
import torch
from sklearn.preprocessing import StandardScaler

from thermal_model_trainer import ThermalMLP

FEATURE_NAMES = [
    "lst",
    "local_mean_3x3",
    "local_std_3x3",
    "local_min_3x3",
    "local_max_3x3",
    "local_range_3x3",
]


def _compute_local_features(raw: np.ndarray, window_size: int = 3) -> dict[str, np.ndarray]:
    if raw.ndim != 2 or raw.size == 0:
        raise ValueError("ST_B10 raster must be a non-empty two-dimensional array.")
    if window_size < 1 or window_size % 2 == 0:
        raise ValueError("window_size must be a positive odd integer.")

    pad = window_size // 2
    features = {
        "lst": raw,
        "local_mean_3x3": np.full_like(raw, np.nan, dtype=np.float32),
        "local_std_3x3": np.full_like(raw, np.nan, dtype=np.float32),
        "local_min_3x3": np.full_like(raw, np.nan, dtype=np.float32),
        "local_max_3x3": np.full_like(raw, np.nan, dtype=np.float32),
        "local_range_3x3": np.full_like(raw, np.nan, dtype=np.float32),
    }

    for row in range(raw.shape[0]):
        for col in range(raw.shape[1]):
            window = raw[
                max(0, row - pad) : min(raw.shape[0], row + pad + 1),
                max(0, col - pad) : min(raw.shape[1], col + pad + 1),
            ]
            valid_window = window[np.isfinite(window)]
            if valid_window.size == 0:
                continue
            features["local_mean_3x3"][row, col] = np.mean(valid_window)
            features["local_std_3x3"][row, col] = np.std(valid_window)
            features["local_min_3x3"][row, col] = np.min(valid_window)
            features["local_max_3x3"][row, col] = np.max(valid_window)
            features["local_range_3x3"][row, col] = np.ptp(valid_window)

    return features


def build_thermal_feature_cube(
    raster_path: str | Path,
    nodata_value: float | None = None,
) -> np.ndarray:
    """Return an H×W×6 feature cube for one selected-area ST_B10 raster."""

    with rasterio.open(raster_path) as source:
        if source.count != 1:
            raise ValueError(f"Expected one thermal band, found {source.count}.")
        raw = source.read(1).astype(np.float32)
        if nodata_value is None:
            nodata_value = source.nodata

    if nodata_value is not None:
        raw[raw == nodata_value] = np.nan
    raw = np.nan_to_num(raw, nan=np.nan, posinf=np.nan, neginf=np.nan)
    local_features = _compute_local_features(raw)
    cube = np.stack([local_features[name] for name in FEATURE_NAMES], axis=-1)
    return cube


def load_model_artifacts(model_dir: str | Path) -> tuple[ThermalMLP, StandardScaler, dict[str, Any]]:
    """Load the saved thermal model, scaler, and model metadata."""

    model_dir = Path(model_dir)
    model_path = model_dir / "thermal_model.pt"
    scaler_path = model_dir / "thermal_scaler.joblib"
    metrics_path = model_dir / "thermal_model_metrics.json"
    if not model_path.is_file() or not scaler_path.is_file() or not metrics_path.is_file():
        raise FileNotFoundError(
            "Thermal model artifact set must include thermal_model.pt, "
            "thermal_scaler.joblib, and thermal_model_metrics.json."
        )

    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    feature_names = metrics.get("feature_names", FEATURE_NAMES)
    if feature_names != FEATURE_NAMES:
        raise ValueError(
            f"Unsupported thermal feature schema: {feature_names}; expected {FEATURE_NAMES}."
        )

    model = ThermalMLP(n_features=len(FEATURE_NAMES), n_classes=len(metrics.get("classes", [])))
    model.load_state_dict(torch.load(model_path, map_location="cpu", weights_only=True))
    model.eval()
    scaler = joblib.load(scaler_path)
    if not isinstance(scaler, StandardScaler):
        raise TypeError("thermal_scaler.joblib must contain a StandardScaler.")
    return model, scaler, metrics


def predict_probability_map(
    raster_path: str | Path,
    model: ThermalMLP,
    scaler: StandardScaler,
    metrics: dict[str, Any],
) -> tuple[np.ndarray, np.ndarray]:
    """Predict class probabilities for every valid pixel in a selected raster."""

    feature_cube = build_thermal_feature_cube(raster_path)
    flat_features = feature_cube.reshape(-1, len(FEATURE_NAMES))
    valid = np.isfinite(flat_features).all(axis=1)
    if not np.any(valid):
        raise ValueError("No finite thermal features remain after raster validation.")

    values = scaler.transform(flat_features[valid])
    tensor = torch.tensor(values, dtype=torch.float32)
    with torch.no_grad():
        logits = model(tensor)
        probabilities = torch.softmax(logits, dim=1)

    full_probabilities = np.zeros((flat_features.shape[0], len(metrics["classes"])), dtype=np.float32)
    full_probabilities[valid] = probabilities.numpy()
    valid_mask = valid.reshape(feature_cube.shape[:2])
    return full_probabilities.reshape(feature_cube.shape[:2] + (-1,)), valid_mask


def save_probability_outputs(
    raster_path: str | Path,
    model_dir: str | Path,
    output_dir: str | Path,
) -> dict[str, Any]:
    """Run arbitrary-area inference and save aligned probability and mask rasters."""

    model, scaler, metrics = load_model_artifacts(model_dir)
    with rasterio.open(raster_path) as source:
        transform = source.transform
        crs = source.crs
        nodata = source.nodata

    probabilities, valid_mask = predict_probability_map(
        raster_path,
        model,
        scaler,
        metrics,
    )
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    with rasterio.open(
        output_dir / "thermal_probabilities.tif",
        "w",
        driver="GTiff",
        height=probabilities.shape[0],
        width=probabilities.shape[1],
        count=len(metrics["classes"]),
        dtype="float32",
        crs=crs,
        transform=transform,
        nodata=-9999,
    ) as destination:
        for band_index in range(len(metrics["classes"])):
            output = probabilities[..., band_index].copy()
            output[~valid_mask] = -9999
            destination.write(output, band_index + 1)

    with rasterio.open(
        output_dir / "thermal_valid_mask.tif",
        "w",
        driver="GTiff",
        height=valid_mask.shape[0],
        width=valid_mask.shape[1],
        count=1,
        dtype="uint8",
        crs=crs,
        transform=transform,
        nodata=0,
    ) as destination:
        destination.write(valid_mask.astype(np.uint8), 1)

    return {
        "probability_shape": list(probabilities.shape),
        "valid_pixel_count": int(valid_mask.sum()),
        "classes": metrics["classes"],
        "source_raster": str(raster_path),
    }
