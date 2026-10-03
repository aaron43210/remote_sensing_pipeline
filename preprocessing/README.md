# Preprocessing

**Owner:** Ananthanarayanan

Turns a raw hyperspectral cube into the **16 clean bands** the Hydra network
reads, chosen to cover both agriculture and minerals. It runs on Dask, chunk by chunk, so memory stays bounded whatever the
scene size. The folder is self-contained: it needs no `shared/` or `common/`.

```
raw-data ──▶ preprocessing ──▶ preprocessed-multiband ──▶ ml_inference
```

## What it does, in order

| Step | Why |
|---|---|
| Quality gate on **raw** values | Rejects a scene that is mostly no-data (>50%). Partly cloudy scenes are kept. |
| Scale, haze and bad bands measured **once per scene** | Measuring per chunk would leave seams between chunks. |
| Fill (−9999), NaN, inf → 0; integer reflectance ÷ 10000 | Auto-detected. EMIT is already 0..1. |
| Haze removal (dark-object subtraction) **only for raw radiance** | On EMIT L2A, which is already surface reflectance, it destroys signal: NDVI +0.25 → −0.21 on a real crop. |
| Interpolate water-vapour bands (1340–1460, 1790–1960 nm) and low-SNR bands | Interpolated, never deleted, so band positions never shift. |
| Savitzky-Golay smoothing (7/2) | Keeps the depth of the 2205 / 2350 nm absorption features. |
| Keep the 16 target bands, clip to [0, 1] **last** | Smoothing can ring below 0; clipping earlier lets that through. |

Verified on a real EMIT L2A crop (276×277×285): 0.2 s, and identical to the
earlier numpy pipeline (max difference 0). With 16 bands on a 553×553 EMIT
crop: 1.1 s, every target within 3 nm of a sensor band, none interpolated.

## Input: `raw-data`

The message ingestion already builds, unchanged:

| Field | Meaning |
|---|---|
| `scene_id` | carried through to every output |
| `zarr_path` | `raw-scenes/<zarr_path>`: plain zarr array (rows, cols, bands) |
| `wavelengths` | used if the store has none in `attrs['wavelengths']` |
| `satellite` | `EMIT` / `EnMAP` → already surface reflectance → no haze removal |
| `bbox`, `timestamp`, `tasks` | passed through |

The cube is expected to be **cropped to the user's bbox and orthorectified**.
Messages may be msgpack or JSON.

## Output: `preprocessed-multiband` (msgpack)

| Field | Meaning |
|---|---|
| `data_path` | `preprocessed-data/<data_path>` = `multiband.npy`, (rows, cols, 16) float32, 0..1, no NaN. This is what `ml_inference` reads today. |
| `zarr_path` | the same data as chunked Zarr `(256, 256, 16)`, for Dask/Ray |
| `band_names`, `target_nm`, `wavelengths` | `b450…b2350`, requested nm, actual sensor nm |
| `n_bands`, `shape` | 16 by default; `[rows, cols, n_bands]` |
| `interpolated_bands` | targets that were estimated, not measured (normally `[]`) |
| `dark_subtraction`, `quality` | what was applied; no-data fractions |

**No data** = all bands 0.

## The band order

`bands.py` holds the one list, every band measured (none in a water-vapour window):

| Band (nm) | Used for |
|---|---|
| 450, 550, 680 | blue, green (chlorophyll), red |
| 720, 800 | red edge, NIR (vegetation health, LAI) |
| 860, 900 | iron oxides (hematite, goethite) |
| 970, 1650 | leaf water, crop water stress |
| 2100 | cellulose, crop residue |
| 2165, 2205 | kaolinite doublet / Al-OH clays |
| 2250, 2265 | chlorite / epidote, jarosite |
| 2320, 2350 | Mg-OH, carbonate |

It replaces the old 10-band list, whose 1450 nm input sat inside a
water-vapour window and so was interpolated, not measured. To run the old
model unchanged, set
`PREPROC_TARGET_BANDS="450,680,720,800,900,1450,2205,2265,2320,2350"`.
**The model's input size must equal `n_bands`.** See
[NOTE_FOR_AARON.md](NOTE_FOR_AARON.md).

## Handing off to Ray

`ray.data.from_dask` takes a Dask **dataframe**; this module outputs an array.
`handoff.py` does the conversion (one row per pixel, row-major):

```python
cube = da.from_zarr(store)                       # preprocessed.zarr
ds = ray.data.from_dask(to_dask_dataframe(cube))
image = to_image(predictions, cube.shape)        # back onto the map
```

## Landsat (`thermal/`)

Plain functions with no Kafka, because the pipeline has no thermal stage yet.
Each applies the scene's own MTL calibration (Level-2, not Level-1) and the
QA_PIXEL cloud mask, and reads only the bbox window in any UTM zone:

```python
from thermal import process
celsius, transform, crs, tags = process.thermal_celsius("..._ST_B10.TIF", bbox)
refl, transform, crs, tags = process.surface_reflectance("..._SR_B5.TIF", bbox)
```

Keep each scene's `_MTL.txt` and `_QA_PIXEL.TIF` beside its bands.

## Integration notes

1. **Ingestion should publish to `raw-data`**, not `preprocessed-multiband`.
   Today it skips this stage. Its own `multiband.npy` is then redundant;
   this module writes that file.
2. Add `raw-data` to `KAFKA_CREATE_TOPICS`, unless topic auto-creation is relied on.
3. **ml_inference must expect 16 inputs.** Today it hard-codes 10 and silently
   drops any extra bands, so it would read the wrong ones. The exact change is in
   [NOTE_FOR_AARON.md](NOTE_FOR_AARON.md).

## Configuration

All values are in `config.py`, and every one can be overridden by environment variable:
`PREPROC_INPUT_TOPIC`, `PREPROC_OUTPUT_TOPIC`, `PREPROC_BUCKET_IN`
(`raw-scenes`), `PREPROC_BUCKET_OUT` (`preprocessed-data`), `PREPROC_WRITE_NPY`,
`PREPROC_CHUNK_SIZE`, `PREPROC_DARK_SUBTRACTION` (`auto`/`always`/`never`).
For AWS, set `S3_ENDPOINT_URL=""`.

## Tests

```bash
cd preprocessing
pip install -r requirements.txt pytest
python -m pytest tests/ -v          # 57 tests, ~2 s, no Kafka/MinIO needed
```
