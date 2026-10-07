import json
import tempfile
import unittest
from pathlib import Path

import joblib
import numpy as np
import rasterio
import torch
from sklearn.preprocessing import StandardScaler

from thermal_inference import (
    FEATURE_NAMES,
    build_thermal_feature_cube,
    load_model_artifacts,
    predict_probability_map,
)
from thermal_model_trainer import ThermalMLP


class ThermalInferenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp_dir.name)
        self.raster_path = self.directory / "selected_roi.tif"
        self.model_dir = self.directory / "model"
        self.model_dir.mkdir()

        values = np.array(
            [
                [300.0, 302.0, 304.0],
                [306.0, 308.0, 310.0],
                [312.0, 314.0, 316.0],
            ],
            dtype=np.float32,
        )
        transform = rasterio.transform.from_origin(74.0, 31.1, 0.001, 0.001)
        with rasterio.open(
            self.raster_path,
            "w",
            driver="GTiff",
            height=3,
            width=3,
            count=1,
            dtype=values.dtype,
            crs="EPSG:4326",
            transform=transform,
            nodata=-9999,
        ) as destination:
            destination.write(values, 1)

        model = ThermalMLP(n_features=len(FEATURE_NAMES), n_classes=2)
        torch.save(model.state_dict(), self.model_dir / "thermal_model.pt")
        scaler = StandardScaler().fit(np.zeros((2, len(FEATURE_NAMES)), dtype=np.float32))
        joblib.dump(scaler, self.model_dir / "thermal_scaler.joblib")
        with open(self.model_dir / "thermal_model_metrics.json", "w", encoding="utf-8") as handle:
            json.dump(
                {
                    "feature_names": FEATURE_NAMES,
                    "classes": ["class_0", "class_1"],
                    "n_features": len(FEATURE_NAMES),
                },
                handle,
            )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_features_are_extracted_from_selected_roi_only(self) -> None:
        features = build_thermal_feature_cube(self.raster_path)
        self.assertEqual(features.shape, (3, 3, len(FEATURE_NAMES)))
        self.assertTrue(np.isfinite(features[..., 0]).all())
        self.assertTrue(np.all(features[..., 5] >= 0))

    def test_inference_is_spatially_aligned_and_leakage_safe(self) -> None:
        model, scaler, metrics = load_model_artifacts(self.model_dir)
        probabilities, valid_mask = predict_probability_map(
            self.raster_path,
            model,
            scaler,
            metrics,
        )

        self.assertEqual(probabilities.shape, (3, 3, 2))
        self.assertEqual(valid_mask.shape, (3, 3))
        self.assertTrue(np.allclose(probabilities.sum(axis=-1)[valid_mask], 1.0))
        self.assertEqual(model.network[0].in_features, len(FEATURE_NAMES))
        self.assertEqual(metrics["feature_names"], FEATURE_NAMES)


if __name__ == "__main__":
    unittest.main()
