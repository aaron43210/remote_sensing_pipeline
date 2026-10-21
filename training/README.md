# Training HydraNet

HydraNet is trained to **reproduce the classical methods**: the classical
method labels every training sample (it is the teacher), and the model is
only promoted when its maps match the classical maps.

```bash
source /path/to/py311/bin/activate
pip install -e ../libs/hydra[training] -e ../libs/hs_common[geo] pandas
# 1. real EMIT pixels, cleaned by the deployed preprocessing (see harvest_emit.py for the docker command)
# 2. data, training, verification
python -m datagen --out artifacts/data            # ~2 min; reads ../../MINERAL, AGRICULTURE, THERMAL
python -m train --version v1                      # lightweight MLP only; writes hydra.onnx + model_card.json
python -m verify_maps --model artifacts/models/v1 # whole-scene NN vs classical, incl. real Landsat
python ../scripts/promote_model.py --model-dir artifacts/models/v1 --lut artifacts/data/agri_lut.npz
```

## Data (all teacher-labelled)

| Set | Source | Teacher |
|---|---|---|
| mineral | USGS-library mixtures: the owner's 62,400 + 100k coverage (dark/bright, vegetation, water, soil) + 40k quartz/gypsum near-ties + 60k with **dry vegetation (35 %) and soil moisture (30 %)**; the `training_sample.csv` spectra; **real EMIT bare pixels** from 5 scenes (40k each, `harvest_emit.py`: cleaned by the deployed preprocessing) | SAM + SMA + quality (`hydra/classical/mineral.py`, = `MineralAnalyzer`) |
| mineral_real | `dataset_sample.csv`: the owner's real scene (benchmark) | same teacher; reproduces the CSV's labels exactly (evaluation only) |
| mineral_real_holdout | a whole EMIT scene never trained on (Death Valley, 2023-10-15) | same teacher (evaluation only) |
| agri | 200k PROSAIL spectra (the CSV's exact settings, recovered to RMSE 1e-8, plus soil/structure/sun variation and noise) | PROSAIL LUT inversion, 100k LUT, k = 20 |
| agri_csv | the 10,000 CSV rows | same LUT (evaluation) |
| thermal | 400 synthetic LST scenes (pixels split by scene) | `ThermalAnomalyDetector` rules |

## Decisions made from measurements

* **The mineral gap on real EMIT was the dataset, then the inputs (not the training).** Measured on the
  owner's real scene, abundance error: the prototype 0.078; new data with the old 275 inputs 0.008; new data with the
  530 inputs (raw reflectance added: albedo/slope that unmixing needs) 0.006. The prototype fitted its own
  data to 0.004, so optimisation was never the problem.
* **Labels come from the teacher, not from the updated CSV.** `training_sample.csv` carries the
  simulation's mixing fractions and an SMA-only quality; the benchmark (`dataset_sample.csv`) and production
  use SAM + SMA + combined quality. Training on the CSV labels would teach a different answer from the one
  the model is checked against, so every spectrum is relabelled by the teacher (which reproduces the
  benchmark's labels exactly).
* **Real pixels go through the production preprocessing** before labelling: the network must learn the
  spectra it sees in production (smoothed, bad bands interpolated), not raw L2A.

* **LUT k = 20, not 5.** With k = 5 the classical LAI changes by a lot for sub-percent reflectance changes
  (even a k-NN fit to its own labels reaches only R² 0.90). k = 20 is more accurate against the simulation
  truth for every core parameter *and* lets the network match it (R² 0.97).
* **Near-tie data + high-gain SAM margin input.** Real quartz and gypsum pixels sit 0.001 rad apart (their
  library spectra share half the spectrum); without these, real-scene agreement on quartz was 47 %; with
  them 100 %.
* **MLP-only (no Conv1d), the only architecture kept.** Same agreement as the conv model at equal steps,
  2.5x fewer parameters, 9x faster mineral inference. v1: 502k parameters (raw reflectance adds 33k), ~2 MB ONNX.
* **Agriculture map gate on error relative to range**, not within-scene R² (a field's LAI varies by ~1 of
  a 0-10 range, so the same error gives a far lower R²; it is still reported).
* **Bands interpolated to the exact target wavelengths**, not nearest-band picks.

## Gates

Training (`train.py`, on the ONNX outputs): mineral class ≥ 0.98, abundance/quality MAE ≤ 0.03; on BOTH
real EMIT scenes (owner's benchmark and the held-out scene) class ≥ 0.97, abundance and quality MAE ≤ 0.05;
agriculture R² ≥ 0.95 (Cab, Cw, LAI), thermal agreement ≥ 0.99.
Maps (`verify_maps.py`): mineral class ≥ 0.98, agriculture nMAE ≤ 5 %, thermal ≥ 0.99 on synthetic and
real Landsat scenes. `promote_model.py` refuses a model that fails either, unless `--force` (recorded).

## Changing the feature spec without downtime

The spec hash locks code and model together. Promote the new model FIRST: running services refuse it
(SpecMismatch) and keep serving the old one. Then roll out the new images, which load the new model.
