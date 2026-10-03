# Note for Aaron: the network now gets 16 bands, not 10

**From:** Ananthanarayanan (preprocessing)

Preprocessing now sends **16 bands** on `preprocessed-multiband`, so one small
model can serve agriculture *and* minerals:

```
450, 550, 680, 720, 800, 860, 900, 970, 1650, 2100, 2165, 2205, 2250, 2265, 2320, 2350
```

The message carries `n_bands` (16) and `band_names` (`b450` … `b2350`), in
this exact order. The old 1450 nm input is gone: it sat inside a water-vapour
window, so it was always interpolated, never measured. Checked on a real EMIT
scene: all 16 bands are within 3 nm of a sensor band and none is interpolated.

The model stays small. Going from 10 to 16 inputs adds 384 weights, from about
4.4k to 4.8k parameters.

**I have not touched any of your files.** Below are the changes your side needs.

## 1. Required: match the input size (`model.py:32`)

```python
INPUT_BANDS = 16
```

No trained weights exist yet, so nothing is lost. `_load_model_weights` falls
back to random init today.

## 2. Required: refuse mismatched input instead of truncating (`main.py:168-178`)

Today, if the band count differs, extra bands are cut off and missing ones are
padded with zeros. With 16 bands coming in, the model would silently read the
first 10, which are the wrong bands, and produce maps that look fine but are
wrong. Please fail loudly instead:

```python
n_pixels, n_features = bands_array.shape
if n_features != self.model.n_features:
    raise ValueError(
        f"{scene_id}: got {n_features} bands, model expects "
        f"{self.model.n_features}. Retrain, or set PREPROC_TARGET_BANDS "
        f"to the list the model was trained on.")
```

To run the old 10-band model in the meantime, set this on the preprocessing
container. No code change is needed on either side:
`PREPROC_TARGET_BANDS="450,680,720,800,900,1450,2205,2265,2320,2350"`

## 3. Worth fixing: training cannot run (`train.py:45`)

`train.py` imports `FusionLightweightNet`, `PhysicsConsistencyLoss` and
`TOTAL_FEATURES_HSI`, none of which exist in `model.py` any more, so it fails
with `ImportError`. Also, `IndianPinesDataset` builds 13 hand-made features,
not the bands inference receives, so a model trained on it would not fit the
live input. For training, select the same 16 bands from each training cube by
wavelength (`preprocessing/bands.py::select` does exactly this).

## 4. Second copy of the band list (`ingestion/main.py:43`)

`TARGET_WAVELENGTHS_NM` is the old 10-band list, with 900 nm last. If
ingestion keeps writing its own `multiband.npy`, that file will disagree with
preprocessing. Simplest fix: ingestion publishes `raw-data` and lets
preprocessing write the multiband file (see preprocessing/README.md,
"Integration notes").

## 5. Optional: three indices at the model input

They help a small network a lot and cost three inputs (`INPUT_BANDS = 19`).
Compute them from `band_names` in the message so the order can never slip:

```python
def add_indices(x, names, eps=1e-6):
    b = {n: x[:, k] for k, n in enumerate(names)}
    ndvi = (b["b800"] - b["b680"]) / (b["b800"] + b["b680"] + eps)
    ndmi = (b["b860"] - b["b1650"]) / (b["b860"] + b["b1650"] + eps)
    # Al-OH depth at 2205 against a straight line between 2100 and 2250
    continuum = 0.3 * b["b2100"] + 0.7 * b["b2250"]
    clay = 1.0 - b["b2205"] / (continuum + eps)
    return np.column_stack([x, ndvi, ndmi, clay]).astype(np.float32)
```

Training and inference must both call it.
