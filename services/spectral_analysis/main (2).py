"""FastAPI service for spectral-index neural-network predictions."""
import json
import os
from pathlib import Path

import joblib
import numpy as np
import tensorflow as tf
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from derivative_analysis import (
    FEATURE_NAMES,
    INDEX_NAMES,
    calculate_spectral_indices,
    predict_spectral_indices,
)

ARTIFACT_DIR = Path(os.getenv("SPECTRAL_ARTIFACT_DIR", "artifacts"))
MODEL_PATH = Path(os.getenv(
    "SPECTRAL_MODEL_PATH", str(ARTIFACT_DIR / "spectral_index_nn.keras")
))
SCALER_PATH = Path(os.getenv(
    "SPECTRAL_SCALER_PATH", str(ARTIFACT_DIR / "input_scaler.joblib")
))
METADATA_PATH = Path(os.getenv(
    "SPECTRAL_METADATA_PATH", str(ARTIFACT_DIR / "model_metadata.json")
))

app = FastAPI(title="Spectral Analysis Service", version="1.0.0")
model = None
scaler = None
metadata = {}


@app.on_event("startup")
def load_artifacts():
    global model, scaler, metadata
    if not MODEL_PATH.is_file():
        raise RuntimeError(f"Missing model: {MODEL_PATH}")
    if not SCALER_PATH.is_file():
        raise RuntimeError(f"Missing scaler: {SCALER_PATH}")

    model = tf.keras.models.load_model(MODEL_PATH)
    scaler = joblib.load(SCALER_PATH)

    if METADATA_PATH.is_file():
        with METADATA_PATH.open(encoding="utf-8") as f:
            metadata = json.load(f)


class SpectralRequest(BaseModel):
    # Rows ordered Blue, Green, Red, NIR, SWIR1.
    reflectance: list[list[float]] = Field(..., min_length=1)


@app.get("/health")
def health():
    return {"status": "ok" if model is not None else "not_ready"}


@app.get("/metadata")
def get_metadata():
    return {
        "input_features": list(FEATURE_NAMES),
        "output_indices": list(INDEX_NAMES),
        "metadata": metadata,
    }


@app.post("/predict")
def predict(request: SpectralRequest):
    try:
        x = np.asarray(request.reflectance, dtype=np.float32)
        if x.ndim != 2 or x.shape[1] != 5:
            raise ValueError("Each pixel needs five bands.")
        if not np.all(np.isfinite(x)) or np.any(x < 0):
            raise ValueError("Inputs must be finite and non-negative.")

        y = predict_spectral_indices(x, model, scaler)
        return {
            "output_indices": list(INDEX_NAMES),
            "predictions": y.tolist(),
        }
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/calculate")
def calculate(request: SpectralRequest):
    try:
        x = np.asarray(request.reflectance, dtype=np.float32)
        if x.ndim != 2 or x.shape[1] != 5:
            raise ValueError("Each pixel needs five bands.")
        if not np.all(np.isfinite(x)):
            raise ValueError("Inputs must be finite.")

        y = calculate_spectral_indices(x)
        return {"output_indices": list(INDEX_NAMES), "values": y.tolist()}
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
