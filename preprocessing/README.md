# Preprocessing

**Owner:** Ananthanarayanan

Cleans the raw EMIT cube that ingestion cropped to the user's box: every band
is kept (HydraNet reads the full spectrum), and a 16-band quick-look product
is written beside it. It runs on Dask, chunk by chunk, so memory stays bounded
whatever the scene size. `thermal_service.py` does the same job for Landsat.

```
raw.hyperspectral ──▶ service.py         ──▶ preprocessed.hyperspectral ──▶ ml_inference
raw.thermal       ──▶ thermal_service.py ──▶ preprocessed.thermal       ──▶ ml_inference
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

## Input: `raw.hyperspectral`

`RawHyperspectral` in `libs/hs_common/hs_common/messages.py` (JSON schema in
`contracts/`). The cube is already cropped to the bbox and orthorectified.

| Field | Meaning |
|---|---|
| `zarr_key` | `silver/{job}/raw.zarr` in `S3_BUCKET`: (rows, cols, bands) |
| `wavelengths`, `good_bands` | used if the store's attrs have none |
| `satellite` | `EMIT` is already surface reflectance, so no haze removal |
| `cloud_key`, `georef`, `bbox`, `tasks`, `source` | passed through |

## Output: `preprocessed.hyperspectral`

| Field | Meaning |
|---|---|
| `reflectance_key` | `silver/{job}/reflectance.zarr`: every band, cleaned, 0..1, no NaN. HydraNet's input. |
| `multiband_key` | `silver/{job}/multiband.zarr`: the 16 target bands below |
| `wavelengths` | sensor wavelengths of `reflectance.zarr` |
| `quality` | no-data fractions, bad-band count, scale, dark subtraction, seconds |

**No data** = all bands 0. A scene that fails the quality gate is a permanent
error: the job reports it instead of retrying.

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
These bands only feed the quick-look product; HydraNet reads every band.

## Landsat (`thermal/`, `thermal_service.py`)

`raw.thermal` → land surface temperature in °C as a COG at
`silver/{job}/thermal/lst.tif` → `preprocessed.thermal`. Each scene's own MTL
calibration (Level-2) and QA_PIXEL cloud mask are applied, and only the bbox
window is read, in any UTM zone:

```python
from thermal import process
celsius, transform, crs, tags = process.thermal_celsius("..._ST_B10.TIF", bbox)
```

Keep each scene's `_MTL.txt` and `_QA_PIXEL.TIF` beside its bands.

## Configuration

Every tunable number is in `config.py` and can be overridden by environment
variable (`PREPROC_CHUNK_SIZE`, `PREPROC_DARK_SUBTRACTION` = `auto`/`always`/`never`,
`PREPROC_MAX_NODATA_FRACTION`, `PREPROC_TARGET_BANDS`, ...). Kafka and object
storage come from the shared platform settings in `hs_common`
(`KAFKA_BOOTSTRAP_SERVERS`, `S3_BUCKET`, `S3_ENDPOINT_URL`; empty endpoint = AWS S3).

## Tests

```bash
cd preprocessing
pip install -r requirements.txt pytest
python -m pytest tests/ -v          # 46 tests, ~2 s, no Kafka/MinIO needed
```
