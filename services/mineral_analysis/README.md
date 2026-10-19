# Mineral Analysis — EMIT reflectance → NN training dataset

This service turns a NASA EMIT L2A reflectance scene into a mineral training
dataset (features + SAM/SMA labels) for a neural network. It also runs the
"normal" SAM + SMA + quality pipeline that produces the reference maps/labels.

## Folder layout

```
real_emit.zarr/                 SOURCE data (compressed EMIT cube, 553x553x285, float32)
band_metadata.json              per-band wavelengths (nm) + good_quality flags (285 bands, 255 good)
real_emit_multiband_cube.tif    extracted georeferenced cube (EPSG:4326) — apply/evaluate the NN here
data/usgs_library/*.csv         12 reference mineral spectra (endmembers)

config.py raster_io.py          core pipeline (I/O, config)
spectral_library.py wavelength_alignment.py
sam.py sma.py continuum.py confidence.py
main.py                         normal SAM+SMA+quality pipeline (reference maps/labels)
mineral_analysis.py             MineralAnalyzer class (alt API, used by tests)
zarr_to_tif.py make_bsq.py      data extraction helpers (zarr -> cube.tif / .bsq)

rtm_inversion/                  dataset build + NN self-validation
  library_setup.py features.py  library alignment + reflectance->feature transform
  build_dataset.py              features + SAM/SMA labels -> output/dataset/
  validate_nn.py                small sklearn MLP sanity check vs SAM/SMA
  run.py                        end-to-end driver

output/
  normal_sam/                   reference SAM/SMA/quality maps + processing_metadata.json
  training_set/                 SYNTHETIC balanced 12-mineral training set (train the NN here)
  dataset/                      REAL-image set (SAM/SMA labels) — use as the evaluation set
  validation_synth/             learnability check on the training set
```

## Regenerate from scratch

```bash
# from this folder; interpreter: Python 3.13 with rasterio, zarr<3, numcodecs, scikit-learn
python zarr_to_tif.py                                           # zarr -> cube.tif (+ .bsq)
MINERAL_OUTPUT_DIR=output/normal_sam python main.py --good-bands-only   # reference maps
python rtm_inversion/build_dataset.py --normal-dir output/normal_sam    # real EVAL set  -> output/dataset/
python rtm_inversion/synth.py --n-samples 60000                         # synthetic TRAIN set -> output/training_set/
```

(`python -m rtm_inversion.run --good-bands-only` chains all three.)

## Training set — `output/training_set/training_set.npz`  (use this to train)

Synthetic, balanced over **all 12 minerals** (~5.2k dominant each), simulated from
the USGS library forward model (`rtm_inversion/synth.py`):
`r = (Σ aᵢ·Eᵢ)·brightness + noise`, with constrained linear mixing (`aᵢ≥0`, `Σaᵢ=1`).

| key            | shape        | meaning                                                        |
|----------------|--------------|----------------------------------------------------------------|
| `X`            | (62400, 275) | features (float32), all finite                                 |
| `y_abund`      | (62400, 12)  | **TRUE abundance fractions — primary regression target**       |
| `y_class`      | (62400,)     | `argmax(y_abund)` dominant-mineral index                       |
| `spectra`      | (62400, 255) | the simulated reflectance (before featurising), if you want raw |
| `feature_names`, `class_names`, `wavelengths_nm` | — | labels / metadata        |

Learnability check (`output/validation_synth/`): a small MLP reaches **class
agreement 0.96 / abundance R² 0.99** on held-out synthetic data — the set is clean
and learnable.

## Evaluation set — `output/dataset/dataset.npz`  (use this to evaluate)

The **real EMIT pixels** with SAM/SMA pseudo-labels, same 275-feature layout.
Apply the trained NN here and compare to SAM/SMA. Caveats for this scene:
only ~5–6 minerals actually occur (SMA abundance dominated by chlorite/hematite/
goethite); the SAM dominant class is degenerate (gypsum/quartz) and disagrees with
the abundances — so evaluate against `y_abund`, and expect only the minerals that
physically occur to be predictable here.

## Feature layout (both sets, 275 dims)

continuum-removed reflectance per good band (255) + diagnostic absorption depths (7)
+ SAM angle to each endmember (12) + brightness (1). `class_names` order == abundance
column order. `schema.json` in each dir has the full column description; a 2000-row
`*_sample.csv` preview sits beside each `.npz`.
