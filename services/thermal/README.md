# Thermal-Only Remote Sensing Model Handoff

## Project Status

This folder contains a thermal-only, ROI-driven remote-sensing model pipeline for Landsat 9 ST_B10 data. It is a crop-based model handoff, not a complete full-resolution production service.

## Owner

- **Bainty Kaur** — implementation and model handoff
- **Aaron** — repository recipient and reviewer

## Implemented

- Validates ST_B10 raster and ROI manifest metadata.
- Extracts six local thermal features: LST, local mean, local standard deviation, local minimum, local maximum, and local range.
- Creates a four-class training dataset.
- Trains and saves a PyTorch model.
- Saves the StandardScaler and validation metrics.
- Provides probability-map inference for an input raster.
- Includes focused feature-extraction and inference tests.

## Model Summary

| Item | Value |
|---|---|
| Training samples | 5,000 |
| Features | 6 |
| Classes | 4 |
| Validation accuracy | 0.968 |
| Model type | PyTorch |
| Input raster | Landsat ST_B10 |
| Spatial scope | Crop-based ROI model |
| Resolution | 30 m |
| Study area | Bathinda District, Punjab, India |

> The model uses the training crop and local thermal statistics. It does not provide a complete API, Kafka, MinIO, frontend, or full-resolution inference workflow.

## Files to Upload

### Model and training data

- `thermal_training_six_feature.csv`
- `thermal_model_artifacts/thermal_model.pt`
- `thermal_model_artifacts/thermal_scaler.joblib`
- `thermal_model_artifacts/thermal_model_metrics.json`

### Implementation

- `thermal_feature_pipeline.py`
- `thermal_model_trainer.py`
- `thermal_inference.py`
- `thermal_entry_point.py`

### Tests

- `test_thermal_feature_pipeline.py`
- `test_thermal_inference.py`

## Files Not Included

Do not upload temporary or superseded files, including old Bathinda training CSVs, temporary raster fixtures, temporary alignment scripts, diagnostic plots, full-resolution training data, or unrelated API/Kafka/MinIO/frontend integration.

## Run the Model

```powershell
python thermal_entry_point.py `
  --manifest <path-to-roi-manifest.json> `
  --raster <path-to-st-b10-raster.tif> `
  --csv <path-to-output.csv> `
  --model-output thermal_model_artifacts `
  --epochs 20
```

```powershell
python thermal_inference.py `
  --raster <path-to-raster.tif> `
  --manifest <path-to-roi-manifest.json> `
  --output-dir <output-directory>
```

## Validation

```powershell
python -m unittest -v test_thermal_feature_pipeline.py test_thermal_inference.py
```

## Important Notes

1. The model is crop-based and uses local thermal statistics.
2. The model is intentionally separate from the shared hyperspectral pipeline.
3. The 0.968 validation accuracy belongs to the completed training run.
4. Future retraining should regenerate the CSV, model, scaler, and metrics together.
5. The exact PyTorch and scikit-learn versions used for training should be retained with the handoff.
