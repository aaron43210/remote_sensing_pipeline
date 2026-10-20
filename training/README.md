# Training HydraNet

HydraNet is trained to **reproduce the classical methods**: the classical
method labels every training sample (it is the teacher), and the model is
only promoted when its maps match the classical maps.

```bash
source /path/to/py311/bin/activate
pip install -e ../libs/hydra[training] -e ../libs/hs_common[geo] pandas
python -m datagen --out artifacts/data            # ~1 min (PROSAIL on all cores)
python -m train --arch mlp --version v1           # ~6 min on a laptop CPU; writes hydra.onnx + model_card.json
python -m verify_maps --model artifacts/models/v1 # whole-scene NN vs classical, incl. real Landsat
python ../scripts/promote_model.py --model-dir artifacts/models/v1 --lut artifacts/data/agri_lut.npz
```

## Data (all teacher-labelled)

| Set | Source | Teacher |
|---|---|---|
| mineral | owner's synthetic set regenerated exactly (seed 0, verified against `training_sample.csv`), + 100k coverage spectra (dark/bright mixtures, vegetation, water, soil) + 40k near-tie quartz/gypsum spectra | SAM + SMA + quality (`hydra/classical/mineral.py`, = `MineralAnalyzer`) |
| mineral_real | `dataset_sample.csv` (real EMIT) | its published SAM labels / SMA abundances (evaluation only) |
| agri | 200k PROSAIL spectra (the CSV's exact settings, recovered to RMSE 1e-8, plus soil/structure/sun variation and noise) | PROSAIL LUT inversion, 100k LUT, k = 20 |
| agri_csv | the 10,000 CSV rows | same LUT (evaluation) |
| thermal | 400 synthetic LST scenes (pixels split by scene) | `ThermalAnomalyDetector` rules |

## Decisions made from measurements

* **LUT k = 20, not 5.** With k = 5 the classical LAI changes by a lot for sub-percent reflectance changes
  (even a k-NN fit to its own labels reaches only R² 0.90). k = 20 is more accurate against the simulation
  truth for every core parameter *and* lets the network match it (R² 0.97).
* **Near-tie data + high-gain SAM margin input.** Real quartz and gypsum pixels sit 0.001 rad apart (their
  library spectra share half the spectrum); without these, real-scene agreement on quartz was 47 %; with
  them 100 %.
* **MLP-only mineral stem (no Conv1d).** Same agreement as the conv model at equal steps, 2.5x fewer
  parameters (469k vs 1.19M), 1.9 MB ONNX vs 4.8 MB, 9x faster mineral inference, 11x faster training.
* **Agriculture map gate on error relative to range**, not within-scene R² (a field's LAI varies by ~1 of
  a 0-10 range, so the same error gives a far lower R²; it is still reported).
* **Bands interpolated to the exact target wavelengths**, not nearest-band picks.

## Gates

Training (`train.py`, on the ONNX outputs): mineral class ≥ 0.98, abundance/quality MAE ≤ 0.03, real-EMIT
class ≥ 0.97 and abundance MAE ≤ 0.06, agriculture R² ≥ 0.95 (Cab, Cw, LAI), thermal agreement ≥ 0.99.
Maps (`verify_maps.py`): mineral class ≥ 0.98, agriculture nMAE ≤ 5 %, thermal ≥ 0.99 on synthetic and
real Landsat scenes. `promote_model.py` refuses a model that fails either, unless `--force` (recorded).
