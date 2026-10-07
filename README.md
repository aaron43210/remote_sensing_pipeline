# Thermal-Only Remote Sensing Model Handoff

## Project Status

This repository contains a thermal-only, ROI-driven remote-sensing model pipeline for Landsat 9 ST_B10 data.

The implementation is a **crop-based model handoff**, not a full-resolution production service. It includes a six-feature thermal training dataset, a PyTorch model, reusable feature extraction, validation tests, and inference support.

## Owner

- **Bainty Kaur** — implementation and model handoff
- **Aaron** — repository recipient and reviewer

## What Was Implemented

- Extracts six local thermal features from ST_B10 rasters:
  - LST
  - Local mean
  - Local standard deviation
  - Local minimum
  - Local maximum
  - Local range
- Uses ROI manifests to constrain feature extraction to the selected area.
- Creates a four-class training dataset.
- Trains and saves a PyTorch model.
- Saves the StandardScaler used during preprocessing.
- Reports validation metrics in JSON.
- Provides inference for an input raster, ROI manifest, and model directory.
- Includes focused tests for feature extraction and inference behavior.

## Model Summary

| Item | Value |
|---|---:|
| Training samples | 5,000 |
| Features | 6 |
| Classes | 4 |
| Validation accuracy | 0.968 |
| Model | PyTorch |
| Input raster | Landsat ST_B10 |
| Spatial scope | Crop-based ROI model |
| Resolution | 30 m |
| Study area | Bathinda District, Punjab, India |

The model is intentionally limited to the training crop and local thermal statistics. It is not a full-resolution model.

### Thermal model and training data

- `services/thermal/thermal_training_six_feature.csv`
- `services/thermal/thermal_model_artifacts/thermal_model.pt`
- `services/thermal/thermal_model_artifacts/thermal_scaler.joblib`
- `services/thermal/thermal_model_artifacts/thermal_model_metrics.json`

### Model implementation

- `services/thermal/thermal_feature_pipeline.py`
- `services/thermal/thermal_model_trainer.py`
- `services/thermal/thermal_inference.py`
- `services/thermal/thermal_entry_point.py`

### Tests

- `services/thermal/test_thermal_feature_pipeline.py`
- `services/thermal/test_thermal_inference.py`

### Existing thermal service files

The existing tracked thermal service files remain part of the repository and should not be deleted:

- `services/thermal/main.py`
- `services/thermal/config.py`
- `services/thermal/lst_calculator.py`
- `services/thermal/emissivity.py`
- `services/thermal/anomaly_detector.py`
- `services/thermal/suhi.py`
- `services/thermal/test_thermal.py`
- `services/thermal/Dockerfile`
- `services/thermal/requirements.txt`
- `services/thermal/.dockerignore`

## Run the Model

The thermal-only entry point requires a raster path, ROI manifest, output CSV, and model output directory:

```powershell
python services/thermal/thermal_entry_point.py `
  --manifest <path-to-roi-manifest.json> `
  --raster <path-to-st-b10-raster.tif> `
  --csv <path-to-output.csv> `
  --model-output services/thermal/thermal_model_artifacts `
  --epochs 20
```

Inference can be performed with the provided inference module:

```powershell
python services/thermal/thermal_inference.py `
  --raster <path-to-raster.tif> `
  --manifest <path-to-roi-manifest.json> `
  --output-dir <output-directory>
```

## Validation

The focused feature-pipeline and inference tests are the recommended validation set for this handoff. The model artifacts should be loaded before publishing to ensure that the `.pt` file and scaler are compatible with the installed PyTorch and scikit-learn versions.

## Important Notes

1. The model is crop-based and uses local thermal statistics.
2. The model is not intended to be a complete full-resolution remote-sensing system.
3. The model is intentionally separated from the shared hyperspectral workflows.
4. The validation accuracy value in the metrics file describes the completed training run and should be reviewed before any future retraining.
5. The exact model and scaler versions should be maintained with the training environment used to generate them.
