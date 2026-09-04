# Thermal Service — Bainty Kaur

**Owner:** Bainty Kaur  
**Scope:** Physics-based thermal processing (LST, emissivity, anomaly detection)

---

## What This Service Does

1. **Reads** pre-fetched Landsat 9 thermal data from Kafka topic `thermal-raw`  
   *(Data is downloaded by ANANTAHANARAYANAN's ingestion service — you don't need to worry about NASA Earthdata)*

2. **Processes** the data using physics-based algorithms:
   - Land Surface Temperature (LST) calculation
   - Emissivity estimation (NDVI-based)
   - Thermal anomaly detection (global + local z-score)
   - Urban Heat Island (UHI) intensity computation
   - Hotspot detection (connected component analysis)

3. **Publishes** results to Kafka topic `thermal-processed`

4. **Saves** output rasters (COG format) to MinIO bucket `thermal-processed`

---

## Your Folder Structure

```
services/thermal/
├── config.py           ← Your env-var config (edit this if needed)
├── Dockerfile          ← Build instructions (do not touch)
├── requirements.txt    ← Python dependencies
├── main.py             ← Entry point (Kafka consumer loop)
├── lst_calculator.py   ← LST physics + thermal indices
├── emissivity.py       ← NDVI-based emissivity estimation
└── anomaly_detector.py ← Anomaly, UHI, hotspot detection
```

**You only work inside this folder. You do not need to change anything else.**

---

## Environment Variables You Need

Create a `.env` file (or set these in your shell):

```bash
# ── Required ─────────────────────────────────────────────
MINIO_ACCESS_KEY=minioadmin
MINIO_SECRET_KEY=minioadmin
MINIO_ENDPOINT=minio:9000
KAFKA_BOOTSTRAP_SERVERS=kafka:29092

# ── Optional (defaults shown) ─────────────────────────────
REDIS_HOST=redis
REDIS_PORT=6379
PROMETHEUS_PORT=8092
LOG_LEVEL=INFO
```

**You do NOT need EARTHDATA_USERNAME or EARTHDATA_PASSWORD** — those are for the ingestion service.

---

## Kafka Interface

| Direction | Topic | Who sends/receives |
|-----------|-------|--------------------|
| **You read** | `thermal-raw` | ANANTAHANARAYANAN's ingestion publishes here |
| **You write** | `thermal-processed` | Aaron's API gateway reads this |

### Message you receive on `thermal-raw`:
```json
{
  "scene_id": "LANDSAT9_THERMAL_...",
  "thermal_path": "LANDSAT9_THERMAL_.../lst.tif",
  "shape": [512, 512],
  "bbox": [-117.5, 33.0, -117.0, 33.5],
  "sensor": "Landsat 9 TIRS",
  "metadata": { "temp_min_c": 18.2, "temp_max_c": 51.3 }
}
```

### Message you publish to `thermal-processed`:
```json
{
  "scene_id": "LANDSAT9_THERMAL_...",
  "statistics": { "mean_temp_c": 32.1, "std_temp_c": 4.5, "min_temp_c": 18.2, "max_temp_c": 51.3 },
  "uhi": { "uhi_intensity_c": 4.2, "classification": "STRONG" },
  "n_hotspots": 7,
  "products_path": "LANDSAT9_THERMAL_.../"
}
```

---

## MinIO Buckets

| Bucket | Access | What's in it |
|--------|--------|-------------|
| `thermal-data` | **Read only** | LST COG files (written by ingestion) |
| `thermal-processed` | **Read + Write** | Your output rasters (anomaly, classification, z-scores, hotspots) |
| `thermal-results` | **Read + Write** | Your summary JSON files |

---

## Running Locally (with docker-compose)

From the **project root** (ask Aaron to start the stack first):

```bash
# Start the full stack
docker-compose up -d kafka minio redis zookeeper

# Run your service in isolation
docker-compose up thermal
```

Or run directly (after activating your venv):

```bash
cd hyperspectral-pipeline
pip install -r services/thermal/requirements.txt
cd services/thermal
python main.py
```

---

## Running Tests

```bash
# From project root
python -m pytest tests/unit/test_thermal.py -v
```

Tests are pure numpy — no Kafka or MinIO connection needed.

---

## Output Products

| File | Description |
|------|-------------|
| `{scene_id}/anomaly.tif` | Per-pixel deviation from scene mean (°C) |
| `{scene_id}/classification.tif` | 1–5 thermal class (1=very cold, 5=very hot) |
| `{scene_id}/global_z.tif` | Global z-score map |
| `{scene_id}/local_z.tif` | Local neighbourhood z-score map |
| `{scene_id}/hotspots.tif` | Labelled connected hotspot regions |
| `{scene_id}/summary.json` | Full statistics JSON |

---

## Physics Reference

| Algorithm | Method | Reference |
|-----------|--------|-----------|
| LST emissivity correction | Single-channel: `LST = T_b / (1 + (λ/ρ) × T_b × ln(ε))` | Jiménez-Muñoz & Sobrino (2003) |
| Emissivity from NDVI | `ε = ε_soil + Pv × (ε_veg - ε_soil)` | Sobrino et al. (2004) |
| UHI intensity | `UHI = T_urban_mean - T_rural_mean` | Voogt & Oke (2003) |
| Hotspot detection | Connected component labeling (scipy.ndimage) | — |
