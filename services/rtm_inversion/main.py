# ============================================================
# RTM Inversion Service
# ============================================================

# Reusable RTM inference engine


import numpy as np
import joblib


class RTMInversionService:

    def __init__(
        self,
        model_path,
        x_scaler_path,
        y_scaler_path,
        emit_bands_path,
        target_parameters_path
    ):
        self.model = joblib.load(model_path)
        self.x_scaler = joblib.load(x_scaler_path)
        self.y_scaler = joblib.load(y_scaler_path)

        self.emit_bands = np.asarray(
            joblib.load(emit_bands_path),
            dtype=float
        )

        self.target_parameters = list(
            joblib.load(target_parameters_path)
        )

    def predict(self, wavelengths_nm, reflectance):

        wavelengths_nm = np.asarray(
            wavelengths_nm,
            dtype=float
        ).reshape(-1)

        reflectance = np.asarray(
            reflectance,
            dtype=float
        ).reshape(-1)

        if len(wavelengths_nm) != len(reflectance):
            raise ValueError(
                "Wavelength and reflectance arrays must have "
                "the same length."
            )

        if not np.all(np.isfinite(wavelengths_nm)):
            raise ValueError(
                "Wavelength array contains NaN or infinite values."
            )

        if not np.all(np.isfinite(reflectance)):
            raise ValueError(
                "Reflectance array contains NaN or infinite values."
            )

        band_indices = np.array([
            np.argmin(
                np.abs(wavelengths_nm - band)
            )
            for band in self.emit_bands
        ])

        matched_reflectance = reflectance[
            band_indices
        ]

        matched_reflectance = np.clip(
            matched_reflectance,
            0,
            1
        )

        X = matched_reflectance.reshape(1, -1)

        X_scaled = self.x_scaler.transform(X)

        y_scaled = self.model.predict(
            X_scaled
        )

        y = self.y_scaler.inverse_transform(
            y_scaled
        )

        return {
            parameter: float(y[0, i])
            for i, parameter in enumerate(
                self.target_parameters
            )
        }


# REST API

import os

from flask import Flask, request, jsonify



app = Flask(__name__)


SAVE_DIR = os.environ.get("RTM_MODEL_DIR", "/models")


rtm_service = RTMInversionService(
    model_path=f"{SAVE_DIR}/rf_inversion_model.pkl",
    x_scaler_path=f"{SAVE_DIR}/X_scaler_real.pkl",
    y_scaler_path=f"{SAVE_DIR}/y_scaler_real.pkl",
    emit_bands_path=f"{SAVE_DIR}/final_emit_bands.pkl",
    target_parameters_path=f"{SAVE_DIR}/target_parameters.pkl"
)


@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "service": "RTM Inversion Service",
        "status": "healthy"
    })


@app.route("/predict", methods=["POST"])
def predict():

    try:
        data = request.get_json()

        if data is None:
            raise ValueError(
                "Request body must contain valid JSON."
            )

        if "wavelengths_nm" not in data:
            raise ValueError(
                "Missing required field: wavelengths_nm"
            )

        if "reflectance" not in data:
            raise ValueError(
                "Missing required field: reflectance"
            )

        result = rtm_service.predict(
            wavelengths_nm=data["wavelengths_nm"],
            reflectance=data["reflectance"]
        )

        return jsonify({
            "service": "RTM Inversion Service",
            "status": "success",
            "retrieved_parameters": result
        })

    except Exception as e:

        return jsonify({
            "service": "RTM Inversion Service",
            "status": "error",
            "error": {
                "type": type(e).__name__,
                "message": str(e)
            }
        }), 400


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=8000
    )
