from pathlib import Path
import joblib
import numpy as np
import pandas as pd
import tensorflow as tf

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from lut_prosail import bound_predictions

PROJECT_DIR = Path(__file__).resolve().parents[2]
ARTIFACT_DIR = PROJECT_DIR / "models" / "rtm_inversion_nn"

MODEL_PATH = ARTIFACT_DIR / "nn_rtm_inversion_model.keras"
X_SCALER_PATH = ARTIFACT_DIR / "X_scaler_nn.pkl"
Y_SCALER_PATH = ARTIFACT_DIR / "y_scaler_nn.pkl"
BANDS_PATH = ARTIFACT_DIR / "final_emit_bands_nn.pkl"
TARGETS_PATH = ARTIFACT_DIR / "target_parameters_nn.pkl"

# Load only the previously trained neural network and its artifacts.
model = tf.keras.models.load_model(MODEL_PATH, compile=False)
x_scaler = joblib.load(X_SCALER_PATH)
y_scaler = joblib.load(Y_SCALER_PATH)
emit_bands = np.asarray(joblib.load(BANDS_PATH), dtype=float)
target_parameters = list(joblib.load(TARGETS_PATH))

EXPECTED_TARGETS = ["Cab", "Car", "Cw", "Cm", "LAI", "ALA"]

if len(emit_bands) != 24:
    raise RuntimeError(f"Expected 24 trained bands; found {len(emit_bands)}.")

if list(target_parameters) != EXPECTED_TARGETS:
    raise RuntimeError(
        "Saved target order does not match the expected NN target order: "
        f"{target_parameters}"
    )

if model.input_shape[-1] != 24 or model.output_shape[-1] != 6:
    raise RuntimeError(
        f"Unexpected NN dimensions: input={model.input_shape}, "
        f"output={model.output_shape}"
    )

app = FastAPI(
    title="RTM Inversion NN Service",
    description=(
        "Retrieves Cab, Car, Cw, Cm, LAI and ALA from "
        "24 reflectance values using the saved neural network. "
        "Predictions are clipped to configured parameter bounds."
    ),
    version="1.0.0",
)


class InferenceRequest(BaseModel):
    reflectance: list[float] = Field(
        ...,
        min_length=24,
        max_length=24,
        description=(
            "Exactly 24 reflectance values in the trained band order."
        ),
    )


class InferenceResponse(BaseModel):
    parameters: dict[str, float]


def predict_parameters(reflectance):
    values = np.asarray(reflectance, dtype=float)

    if values.shape != (24,):
        raise ValueError("Exactly 24 reflectance values are required.")

    if not np.all(np.isfinite(values)):
        raise ValueError("Reflectance values must all be finite.")

    # Preserve the 24-feature column order used by the trained scaler.
    features = pd.DataFrame(
        [values],
        columns=[f"Reflectance_Band_{i+1}" for i in range(24)],
    )

    scaled_features = x_scaler.transform(features)

    # NN inference only; no retraining or LUT lookup.
    scaled_output = model.predict(scaled_features, verbose=0)
    prediction = y_scaler.inverse_transform(scaled_output)[0]

    raw_parameters = {
        name: float(value)
        for name, value in zip(target_parameters, prediction)
    }

    # Enforce the configured range for each parameter.
    return bound_predictions(raw_parameters)


@app.get("/health")
def health():
    return {
        "status": "ok",
        "model_type": "saved neural network",
        "input_bands": 24,
        "output_parameters": EXPECTED_TARGETS,
    }


@app.post("/predict", response_model=InferenceResponse)
def predict(request: InferenceRequest):
    try:
        result = predict_parameters(request.reflectance)
        return InferenceResponse(parameters=result)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
