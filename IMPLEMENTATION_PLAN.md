# HydraNet Platform: Final Implementation Plan

> **Status (2026-10-20): implemented.** See `README.md` for how to run, test and deploy, and
> `training/README.md` for the model. Changes from this plan, made from measurements:
> the model is **ONNX**, **MLP-only** (469k parameters, no Conv1d), trained to reproduce the
> classical methods (SAM+SMA, PROSAIL LUT with k = 20, the thermal detector); MinIO is built
> from source (prebuilt community images are no longer published); minerals are coloured by
> spectral group on the map (colour-vision-safe palette). Open item: real-EMIT mineral
> abundance agreement (MAE 0.078 vs gate 0.06), see README "Known limitations".

**What we are building:** a production-style geospatial ML and data-engineering platform. A user
draws a box on a map. The platform fetches NASA EMIT hyperspectral and Landsat thermal imagery,
cleans it, and runs **one neural network with three heads (HydraNet)**. The user gets
**agriculture, mineral and thermal maps**. Underneath sits a real data platform: a streaming event
backbone, a layered data lake, a spatial catalog, batch pipelines, data-quality checks, lineage
and analytics.

**Where it runs:** there is no server of our own.

| Environment | Runs on | Object storage | Purpose |
|---|---|---|---|
| **local** | your laptop, Docker Desktop (give it ≥ 8 GB RAM) | **MinIO** | development and demos |
| **ci** | GitHub Actions runners | **MinIO** | automated tests, end-to-end smoke test |
| **aws** | **one EC2 instance in us-west-2** running the same compose stack, plus S3 and CloudFront | **Amazon S3** | the public deployment, on student/new-account credits |

The same images and the same code run in all three. **Only `.env` changes.** If credits run out,
the stack moves to any VM with S3-compatible storage. No code changes.

---

## 1. Key decisions (and why)

| Decision | Choice | Why |
|---|---|---|
| Model | **HydraNet**: 3 input stems → shared trunk → 3 heads, one `model.pt` | The datasets share no input space (§3). The user still wants one network. |
| Event backbone | **Kafka (KRaft mode, no Zookeeper)**, self-hosted in the stack | Decouples stages, allows replay and reprocessing, scales consumers, provides a DLQ, and feeds the data lake (§5). Managed Kafka (MSK) is far too expensive for credits. |
| Cache / coordination | **Redis**, self-hosted in the stack | Live status, locks, idempotency, rate limits, result reuse (§5.2) |
| Metadata DB | **PostgreSQL + PostGIS**, self-hosted, backed up nightly to S3 | Jobs, catalog with spatial footprints, lineage, model registry. PostGIS answers "do we already have imagery covering this box?" |
| Object storage | **S3 API only** (boto3/s3fs in Python, minio-go with a credential chain in Go) | MinIO locally, S3 on AWS, with no code difference |
| Data lake | **Bronze / Silver / Gold** prefixes in one bucket, with Zarr, COG and Parquet formats | Standard lakehouse layering. Each format suits its data. |
| Catalog | **STAC** items (pystac) + PostGIS table | The geospatial industry standard, and queryable |
| Batch orchestration | **Dagster** (open source) | Asset-based lineage for datasets → model → maps, light on memory. Airflow is the swap-in if you prefer it, but it needs about 2–3 GB more RAM. |
| Analytics | Parquet in Gold + **DuckDB** (local) / **Athena** (AWS) | SQL over pipeline events and map statistics, pay-per-query |
| Deployment | **Single EC2 + docker compose**, Terraform, CloudFront | Cheapest production-shaped setup. §15 lists the scale-up path. |
| Region | **us-west-2 (Oregon)** | NASA Earthdata Cloud (EMIT) and the Landsat public bucket live there, so in-region reads are fast and free of transfer charges |

---

## 2. Is each component really used? (Kafka, Redis)

**Kafka is core.** Every stage boundary is a topic:
- Ingestion, preprocessing and inference scale independently (partitions = parallelism).
- A crashed worker loses nothing. The message is re-delivered and consumers are idempotent.
- **Replay:** reprocessing old jobs with a new model version is a re-publish, not a re-download.
- **Data-engineering use:** an `event-sink` consumer streams every event into the lake as Parquet (Bronze). Pipeline analytics (latency per stage, failure rates, model drift) then come from SQL.
- Failed messages go to a **DLQ** after retries, with nothing silently dropped.

**Redis earns its place**, though it's small (~50 MB):

| Key | Use |
|---|---|
| `job:{id}` (hash, 24 h) | Live job status. The UI updates every second without hitting Postgres. |
| `events:{id}` (pub/sub) | Pushes status changes to the browser over **Server-Sent Events**, so the UI doesn't poll |
| `lock:granule:{granule_id}` (SET NX, 15 min) | Two jobs over the same area download a 1.8 GB granule **once** |
| `idem:{user}:{Idempotency-Key}` (24 h) | A double-clicked "Run" creates one job, not two |
| `rl:{user}:{minute}` | Rate limiting that works across gateway replicas |
| `reuse:{bbox_hash}:{tasks}:{date}:{model_ver}` (7 d) | An identical request returns the existing results instantly |

Postgres could technically do all of this, but Redis keeps hot-path load off the database. It is the standard pattern, and it costs nothing extra on the same machine.

---

## 3. Data findings (from profiling the CSVs and the spec)

| Domain | File | Rows | Inputs | Targets |
|---|---|---|---|---|
| Agriculture | `RTM_NN_TRAINING_DATASET_WAVELENGTHS.csv` | 10,000 | 24 reflectance bands, 403–1558 nm | `Cab, Car, Cw, Cm, LAI, ALA` |
| Mineral train | `training_sample.csv` (preview of 62,400) | 2,000 | 275 = 255 CR bands + 7 depths + 12 SAM + brightness | `dominant` (12 classes), `abund_*` (sum = 1) |
| Mineral real | `dataset_sample.csv` (preview of 7,702) | 2,000 | same 275 | pseudo-labels `label_name`, `abund_*`, `quality` |
| Mineral library | 12 USGS spectra | 441 points each | — | — |
| Thermal | `HyperSpectral_Processer_macOS.csv` | 5,000 | `lst` + 3×3 mean/std/min/max/range | **none** |

1. **No shared input space** and no multi-labelled rows. This calls for per-task stems and masked per-task losses.
2. **The 16-band preprocessing output can't feed either spectral head.** Agriculture has no band above 1558 nm, and mineral needs 255 bands. Preprocessing must **also publish the full cleaned cube**.
3. **The real mineral pseudo-labels contradict each other.** SAM `label_name` agrees with `argmax(abund_*)` in **0 / 2,000** rows. They can be used for domain-gap reporting only, never as accuracy ground truth.
4. `depth_Mg-OH == depth_CO3` in 1,994/2,000 real rows but only 1,178/2,000 synthetic rows. The feature code differs between the two, so **we regenerate the training features with our own code** to get train/inference parity.
5. Domain gap: synthetic depths reach 0.997 (real ≤ 0.65) and brightness 0.11–0.66 (real 0.13–0.30). Augment and report it.
6. Agriculture bands 1357 and 1417 nm sit on the water-vapour edges. **Default 22 bands**, with the 24-band variant as an ablation.
7. Thermal is unlabelled, so v1 uses scene-relative heat-zone pseudo-labels plus a self-supervised anomaly score, stated plainly in the model card. **v2 uses NASA FIRMS active-fire points as real labels.**
8. `quartz.csv` and `gypsum.csv` are identical for their first ~180 points, and so are `illite`/`montmorillonite` for ~163 points. Confirm with the mineral owner before synthesis.

## 4. Current code: blocking bugs to fix first (Phase 0)

| # | Problem |
|---|---|
| B1 | Compose build contexts point one folder too high (`ml_inference/ml_inference`, `frontend/frontend`). There are two ingestion variants. |
| B2 | The Go Dockerfiles copy `go.mod go.sum` from the wrong context. There is no `go.sum`, and go.mod is missing `google/uuid` and `x/crypto`. |
| B3 | `status.go` uses `fmt` without importing it, so it doesn't compile. |
| B4 | The gateway `DATABASE_URL` lacks `sslmode=disable`. `schema.sql` is never applied. |
| B5 | ml_inference: the Dockerfile references `services/`, `shared/` and missing classes. `main.py` imports a missing `shared.*`. `train.py` imports missing names. |
| B6 | The frontend Dockerfile uses `apt-get` on Alpine and Node 18 (Vite 8 needs ≥ 20.19). The API URL is wrong, there is no `/api` prefix and no JWT, `bbox` is sent as an object, and `tasks` is dropped. |
| B7 | Presigned URLs are signed for `minio:9000`, which the browser can't reach. |
| B8 | Python sends msgpack, but the Go orchestrator decodes JSON. |
| B9 | Nobody writes in-progress or error statuses, so the UI polls forever. Status in Redis expires after 1 h. |
| B10 | Ingestion skips preprocessing, reads the wrong netCDF paths, does no orthorectification or bbox crop, and has no cloud or coverage filter. |
| B11 | `_fetch_enmap` returns **random data** as a scene. It must raise instead. |
| B12 | `KAFKA_CREATE_TOPICS` is ignored by Confluent images, so topics get 1 partition and replicas sit idle. |
| B13 | Thermal is not wired: nobody consumes `thermal-raw`. No output has a CRS or transform. |
| B14 | Map layers are mock rectangles. Results are `.npy`, which a browser can't render. |
| B15 | Inference is a single giant tensor, and silent pad/truncate feeds the wrong bands. |
| B16 | `pre_production/` is a nested git repo inside the outer repo. Pick one. |

---

## 5. Architecture

### 5.1 Online path (per user request, streaming)

```
Browser ──HTTPS──► CloudFront ──/api/*──► EC2: nginx ─► api_gateway (Go)
   ▲  SSE status                                     │  Postgres txn: jobs + outbox
   │                                                 ▼  outbox relay
   │                                       Kafka: ingest.requests
   │                                                 ▼
   │                         ingestion (Py) ── EMIT search/stage ─► S3 bronze/silver
   │                              │  └──── Landsat search/stage ──► S3 bronze/silver
   │                              ▼ raw.hyperspectral      ▼ raw.thermal
   │                    preprocessing (Py, Dask)   preprocessing-thermal (Py)
   │                              ▼ preprocessed.hyperspectral ▼ preprocessed.thermal
   │                                   ml_inference (HydraNet)
   │                                              ▼ ml.results (one per task)
   └──── Redis pub/sub ◄── orchestrator (Go) ◄── job.status (from every service)
                                │                 │
                                ▼                 ▼
                      Postgres/PostGIS      event-sink (Py) ──► S3 bronze/events (Parquet)
```

### 5.2 Batch path (Dagster, scheduled)

| Asset / job | Schedule | Does |
|---|---|---|
| `region_granule_index` | daily | Searches NASA for new EMIT and Landsat over the allowed region and upserts footprints into the PostGIS catalog |
| `prestaged_granules` | daily | Pre-downloads the newest clear granules into Bronze, so user jobs rarely wait on NASA |
| `training_agri`, `training_mineral`, `training_thermal` | on change | Builds versioned training sets (PROSAIL generation, mineral synthesis, thermal labels) as Parquet in Gold, each with a DQ report |
| `hydra_model` | manual / weekly | Trains, evaluates against the gates, writes the model card, registers the candidate |
| `hydra_promotion` | manual | Promotes the candidate to `active` after the gates pass |
| `events_compacted` | hourly | Compacts the event-sink's small Parquet files into daily partitions |
| `gold_job_facts`, `gold_layer_stats` | hourly | Analytics tables (§9) |
| `drift_report` | daily | Compares live feature distributions with the training ones and alerts on drift |
| `lifecycle_cleanup`, `pg_backup` | nightly | Expires Bronze/Silver per policy and writes a `pg_dump` to S3 |

Dagster shows lineage: **granule → silver scene → prediction layer**, and **training set → model version → every map made with it**.

---

## 6. Storage and data lake

### 6.1 One bucket, layered prefixes

S3 bucket names are global, and one bucket means one IAM policy and one lifecycle configuration.
Locally the bucket is `hyperspectral-local` (MinIO). On AWS it is `hyperspectral-<yourname>-prod`.

| Layer | Prefix | Format | Lifecycle |
|---|---|---|---|
| **Bronze** (raw, immutable) | `bronze/emit/{granule_id}/*.nc`, `bronze/landsat/{scene_id}/*.TIF`, `bronze/events/topic={t}/dt={d}/*.parquet` | as delivered / Parquet | granules 30 d, events kept |
| **Silver** (cleaned, georeferenced, per job) | `silver/{job_id}/raw.zarr`, `reflectance.zarr`, `multiband.zarr`, `thermal/lst.tif` | Zarr (chunked 256×256×bands), COG | 14 d |
| **Gold** (products) | `gold/results/{job_id}/{task}/{layer}.tif` + `.png` + `manifest.json` + STAC item; `gold/tables/{job_facts,layer_stats,events_daily}/` ; `gold/training/{domain}/v{n}/` | COG, PNG, JSON, Parquet | results 180 d, tables kept |
| **Models** | `models/hydra/{version}/model.pt`, `model_card.json` | — | versioning on |
| **Ops** | `ops/pg_backups/{date}.sql.gz` | — | 30 d |

### 6.2 Environment contract (identical keys everywhere)

```env
ENV=local|ci|aws
S3_BUCKET=hyperspectral-local
S3_ENDPOINT_URL=http://minio:9000            # aws: empty
S3_PUBLIC_ENDPOINT_URL=http://localhost:9000 # aws: empty (presigned URLs go to s3.amazonaws.com)
S3_REGION=us-west-2
S3_FORCE_PATH_STYLE=true                     # aws: false
S3_CREATE_BUCKET=true                        # aws: false (Terraform owns it)
AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY    # local: MinIO user; aws: NOT set, the EC2 instance role is used
```

### 6.3 Code

- `libs/hs_common/storage.py` (Python, shared): `put/get/exists/presign`, `zarr_store(key)`, `cog_path(key)` (GDAL `/vsis3/` with the same env). Every `minio` import is removed.
- `api_gateway/storage/s3.go`: an **internal** client for reads and writes and a **public** client for presigning only (fixes B7). Credentials come from `NewChainCredentials(EnvAWS, EnvMinio, IAM)`.
- MinIO image pinned to a specific release tag, never `latest`. If that image is ever unavailable, any S3-compatible server works, because nothing is MinIO-specific.
- A `minio-init` one-shot container creates the bucket, the lifecycle rules and CORS locally. Terraform does the same on AWS.

### 6.4 Catalog (PostGIS + STAC)

- `catalog_granules(granule_id, sensor, acquired_at, cloud_pct, footprint geometry, bronze_key, status)`. A GiST index on `footprint` makes it fast to find a staged granule covering a bbox.
- Every Gold result also gets a **STAC Item** (bbox, datetime, assets = layer COGs, properties = model version and source granules), so the outputs open in QGIS or any STAC browser.

---

## 7. Kafka design

- Image: `apache/kafka` (KRaft, single broker), pinned. A `kafka-init` container creates topics explicitly.
- Messages are **JSON** with `schema_version` and a unique `event_id`. Schemas are **JSON Schema files in `contracts/`**. Python pydantic models and Go structs are tested against them in CI.
- Delivery is **at-least-once**, with **idempotent consumers**: outputs go to deterministic keys (`silver/{job_id}/…`), and `processed_events(event_id)` dedupes. Offsets are committed after the work is done.
- Retries: 3 in-process retries with backoff, then `dlq` with the error attached. The orchestrator marks the task FAILED and shows the reason.
- **Transactional outbox** in the gateway: the job row and the outbox row are written in one Postgres transaction, and a relay goroutine publishes and marks the row sent. No job can exist without its message, or the reverse.
- An orchestrator **watchdog** marks a stage TIMED_OUT if it makes no progress within its SLA (ingest 15 min, preprocess 5 min, inference 2 min).

| Topic | Partitions | Producer → Consumer | Payload (essentials) |
|---|---|---|---|
| `ingest.requests` | 4 | gateway → ingestion | `job_id, user_id, bbox[4], tasks[], date_from, date_to` |
| `raw.hyperspectral` | 4 | ingestion → preprocessing | `job_id, granule_id, acquired_at, zarr_key, wavelengths, good_bands, crs, transform` |
| `raw.thermal` | 2 | ingestion → preprocessing-thermal | `job_id, landsat_scene_id, acquired_at, st_key, qa_key, mtl_key, bbox` |
| `preprocessed.hyperspectral` | 4 | preprocessing → ml_inference | `job_id, reflectance_key, multiband_key, band_names, wavelengths, crs, transform, quality` |
| `preprocessed.thermal` | 2 | preprocessing-thermal → ml_inference | `job_id, lst_key, crs, transform, cloud_fraction` |
| `ml.results` | 4 | ml_inference → orchestrator | `job_id, task, model_version, layers[{name, tif_key, png_key, bounds, units, colormap, vmin, vmax, stats}], timings` |
| `job.status` | 4 | all → orchestrator | `job_id, stage, task?, state, message, ts` |
| `dlq` | 1 | all → humans / Grafana alert | original message + error + service |

The `event-sink` consumer group reads **all topics** and writes Bronze Parquet with pyarrow, using 1-minute or 5,000-event micro-batches.

---

## 8. Postgres / PostGIS schema (migrations with golang-migrate)

```
users(id, email, password_hash, name, created_at)
jobs(job_id PK, user_id FK, bbox geometry(Polygon,4326), tasks text[], date_from, date_to,
     status, error, model_version, reused_from_job, created_at, completed_at)
job_tasks(job_id, task, stage, state, started_at, finished_at, error)          PK(job_id, task)
job_sources(job_id, sensor, granule_id, acquired_at)                           -- lineage
layers(job_id, task, name, tif_key, png_key, bounds, units, colormap, vmin, vmax, stats jsonb)
catalog_granules(granule_id PK, sensor, acquired_at, cloud_pct, footprint geometry, bronze_key, status)
model_versions(version PK, card jsonb, metrics jsonb, s3_key, feature_spec_hash, active bool, promoted_at)
outbox(id, topic, key, payload jsonb, created_at, sent_at)
processed_events(event_id PK, consumer, processed_at)
dq_results(run_id, dataset, check_name, passed bool, observed jsonb, ts)
```

---

## 9. Data quality, lineage, analytics

**Data-quality checks** (pandera / pydantic, results stored in `dq_results`; a hard failure stops the stage with a clear error):

| Stage | Checks |
|---|---|
| Ingestion | bbox coverage ≥ 90 %, cloud ≤ threshold, wavelengths monotonic, band count as expected, CRS present |
| Preprocessing | no NaN/inf, reflectance in [0, 1], nodata ≤ 50 %, interpolated targets reported |
| Training sets | schema, ranges, abundance sum = 1 ± 1e-3, class balance, no duplicates, train/test hash disjoint |
| Inference | the feature-spec hash matches the model, predicted ranges are physically plausible, fraction of "unclassified" pixels is reported |
| Drift | KS distance between live and training features above threshold → Grafana alert |

**Lineage:** every `manifest.json` records the granule IDs, model version, feature-spec hash, git SHA and code image tags. `job_sources` and `layers` make lineage queryable, and Dagster shows the asset graph.

**Gold analytics tables** (Parquet, queried with DuckDB locally and Athena on AWS, with Glue tables from Terraform):
- `job_facts`: job, user, bbox area, tasks, per-stage durations, status, model version, reuse hit
- `layer_stats`: per job/layer mean, p5, p50, p95, valid %, mineral class composition
- `events_daily`: event counts and latencies per topic and stage

Grafana dashboards: pipeline health (Prometheus), job funnel and latency (Postgres), map statistics over time (Postgres/Athena).

---

## 10. Services (what each one does after the rewrite)

### 10.1 api_gateway (Go, Gin)
- `POST /api/jobs` (JWT, `Idempotency-Key`): validates the bbox (≤ 0.1° per side, inside the allowed region) and the tasks. It checks the Redis reuse key, writes job + outbox, and returns `202 {job_id}`.
- `GET /api/jobs`, `GET /api/jobs/:id`, `GET /api/jobs/:id/events` (SSE), `GET /api/jobs/:id/layers` (presigned URLs from the public client), `GET /api/model`, `GET /api/catalog?bbox=` (available imagery dates).
- Every `:id` route checks job ownership. Auth: bcrypt + HS256 JWT, a secret of ≥ 32 bytes enforced at startup. `/health` and `/metrics`.

### 10.2 orchestrator (Go)
Consumes `job.status` and `ml.results`, updates `jobs`, `job_tasks` and `layers`, writes the Redis hash and publishes to `events:{id}`. It computes the job state (COMPLETED / PARTIAL / FAILED) from the requested tasks and runs the watchdog.

### 10.3 ingestion (Python; merge both variants)
- **EMIT:** search with `earthaccess` (`EMITL2ARFL` + `EMITL2AMASK`), and check the PostGIS catalog first. On AWS (us-west-2), open the granule **directly from NASA's S3** with temporary Earthdata credentials and read only the swath rows and columns the bbox needs, found through the GLT. Locally, download once to Bronze under a Redis lock, then crop. **Orthorectify via the GLT** to EPSG:4326 and write `silver/{job_id}/raw.zarr` with `wavelengths, fwhm, good_wavelengths, crs, transform, granule_id, acquired_at`.
- **Landsat C2 L2:** search a STAC API (Earth Search or Planetary Computer `landsat-c2-l2`; verify the current `earthaccess` short-name in P3). Do **windowed COG reads** of only the bbox for `ST_B10`, `QA_PIXEL` and `MTL`, and stage them in Bronze/Silver.
- Both branches run concurrently. NO_DATA is reported cleanly. EnMAP raises `NotImplemented`, never random data.

### 10.4 preprocessing (Python, Dask; owner Ananthanarayanan, so agree on the contract first)
- Existing steps, plus **`reflectance.zarr`** (all bands, cleaned) beside `multiband.zarr` (16 bands, kept for NDVI/NDMI/clay quick-looks). `multiband.npy` is retired.
- Uses EMIT's `good_wavelengths` mask when present. Carries `crs` and `transform`. JSON messages. Status events.
- **`thermal_main.py`** worker: `thermal.process.thermal_celsius()` → `silver/{job_id}/thermal/lst.tif` (COG).

### 10.5 ml_inference (Python, HydraNet; §11)
Two consumers (hyperspectral, thermal). It runs features and the model in 256×256 tiles with Dask `map_blocks` and applies the masks. It writes each layer as a COG (download/GIS) **and** a colourised PNG (display), plus `manifest.json` and the STAC item. It publishes one `ml.results` per task. The model is loaded from `models/hydra/{active}` at start and **refuses to run on a feature-spec hash mismatch**.

### 10.6 event-sink (Python, new)
All topics → Bronze Parquet, partitioned by topic and date.

### 10.7 frontend (React + Leaflet, served by nginx)
- Login/register, with a token interceptor. Job panel: bbox, tasks, date range, a "use cached imagery" hint from `/api/catalog`.
- A live **per-task timeline over SSE** with real errors.
- Layers: `L.imageOverlay(png, bounds)` + opacity + legend.
  - Agriculture tab: LAI/Cab/Cw/Cm/Car/ALA selector, an uncertainty toggle, and a histogram.
  - Mineral tab: 12-colour class map, confidence, per-mineral abundance, and a composition chart.
  - Thermal tab: LST, heat zones and anomaly, showing the Landsat date versus the EMIT date.
- Pixel inspector client-side: a bbox is ≤ ~200×200 px, so the browser loads the COGs with geotiff.js. COG download buttons and a job history.

---

## 11. HydraNet (the three-in-one network)

### 11.1 Architecture (~150k parameters, CPU)

```
agri_x  (22)  ─► AgriStem    Standardize → Linear 22→128 → GELU → LayerNorm ─────────┐
mineral_x(275)─► MineralStem CR(255): Conv1d 1→16 k7 → Pool → 16→32 k5 → Pool        │ + task embedding
                             → 32→64 k3 → AdaptiveAvgPool(8) → 512                   ├─► Shared trunk
                             scalars(20) → Linear 20→64; concat 576 → Linear → 128 ──┤   3 × ResidualMLP(128,
thermal_x (8) ─► ThermalStem Standardize → Linear 8→64 → GELU → 64→128 ──────────────┘   LayerNorm, GELU, p=0.1)
   Heads:  Agri    → 6 means + 6 log-variances (params in physical units + uncertainty)
           Mineral → 12 class logits + 12 abundances (softmax ⇒ always sums to 1)
           Thermal → 5 heat-zone logits + 8-feature reconstruction (anomaly score)
```

- `forward(x, task)` runs only that stem and head. LayerNorm (not BatchNorm), because batches are per-task.
- Buffers carry normalisation stats, band lists, class names, target ranges and `feature_spec_hash`, so the weights can never meet the wrong features.

### 11.2 Feature package `ml_inference/hydra/features/` (one implementation for training **and** inference)

| Module | Output |
|---|---|
| `agri.py` | the 22 nearest bands to the training wavelengths (fail if > 4 nm away) |
| `mineral.py` | resample to the 255 EMIT good bands → upper-convex-hull continuum removal (numba) → 7 depths → 12 SAM angles vs the CR'd USGS library → brightness |
| `thermal.py` | `lst − median`, `(lst − median)/MAD`, `lst − local_mean`, `local_std`, `local_range`, `local_max − lst`, `local_min − lst`, `lst` (nodata-aware 3×3 via scipy.ndimage) |
| `masks.py` | nodata, cloud (EMIT mask / Landsat QA), water (NDWI), vegetation (NDVI ≥ 0.25), bare |

### 11.3 Training data (built by Dagster, versioned in Gold)

| Head | Data |
|---|---|
| Agriculture | ~200k new PROSAIL samples (`prosail` package, same ranges + soil/N/hotspot variation). **The original 10k CSV is the untouched test set.** |
| Mineral | **Regenerate the 62,400 synthetic set** from the USGS library (sparse Dirichlet over 1–4 minerals, lognormal brightness matched to EMIT, wavelength-dependent noise), then run `features/mineral.py`. `training_sample.csv` is a distribution check, `dataset_sample.csv` a domain-gap report. |
| Thermal | The 5k CSV + augmentation (±15 °C offsets, noise, **injected synthetic hotspots** for anomaly evaluation). Heat-zone labels from robust z: <−1, −1…0.5, 0.5…1.5, 1.5…2.5, >2.5 or local z > 3. |

Spectral augmentation: EMIT-like SNR noise, ±5 % gain, ±2 nm shift, 1–2-band dropout.

### 11.4 Training

- Losses: Gaussian NLL (agriculture), CE + KL (mineral), CE + MSE reconstruction (thermal). They are combined with **learned uncertainty weights** (Kendall et al., 2018).
- Balanced per-task batches (256 per task per step), AdamW 1e-3, cosine schedule, about 60 epochs, early stopping, fixed seeds. MLflow is optional. Dagster records params, metrics and the model card.
- Runs on your laptop CPU in minutes to tens of minutes, so no GPU is needed.

### 11.5 Acceptance gates (each head must beat its classical baseline)

| Head | Gate | Baseline |
|---|---|---|
| Agriculture (original 10k test set) | R² LAI ≥ 0.80, Cab ≥ 0.70, Cw ≥ 0.70. Car, Cm and ALA are reported. | PROSAIL LUT k-NN inversion |
| Mineral (synthetic val) | dominant accuracy ≥ 0.90, abundance MAE ≤ 0.08 | SAM + FCLS |
| Mineral (real) | domain-gap report only (finding 3) | — |
| Thermal | heat-zone agreement ≥ 0.95, injected-hotspot recall ≥ 0.90 at 5 % FPR | z-score rule |
| Multi-task | each head within 2 % of a single-task model, otherwise that head gets its own trunk block | — |

### 11.6 Map rules

Agriculture is shown on vegetated pixels only. Mineral is shown on bare pixels, with "unclassified" when max prob < 0.5 or min SAM > 0.25 rad (the training set has no non-mineral class). Thermal is shown on clear Landsat pixels. Quick-look indices are shown on all valid pixels.

---

## 12. Observability and security

- **Metrics:** Prometheus scraping every service (`/metrics`): consumer lag, stage latency, errors, DLQ count, pixels per second. Grafana dashboards and alerts (DLQ > 0, lag > N, drift, disk > 80 %).
- **Logs:** JSON logs with `job_id` on every line. Locally that's `docker logs`. On AWS the Docker `awslogs` driver ships them to CloudWatch Logs (short retention to save credits).
- **Security:** the EC2 security group allows inbound 80 **only from the CloudFront prefix list**, with no SSH; admin access goes through **SSM Session Manager**. An IAM instance role grants access to the one bucket only. Secrets (DB password, JWT, Earthdata token) live in **SSM Parameter Store** and are rendered into `.env` at deploy. The S3 bucket blocks public access, and the browser uses presigned URLs only. Kafka, Postgres, Redis, Grafana and MinIO are never exposed publicly. Grafana is reached through an SSM port-forward.

---

## 13. Repository layout (target)

```
pre_production/
  docker-compose.yml            # full stack; profiles: core, batch (dagster), obs (prometheus+grafana), dev
  docker-compose.aws.yml        # override: no minio, awslogs driver, resource limits
  .env.example  .env.aws.example
  contracts/                    # JSON Schemas for every topic + fixtures
  libs/hs_common/               # storage.py, messages.py, kafka.py (retry/DLQ/idempotency), status.py, dq.py
  api_gateway/  orchestrator/   # Go (go.mod at root)
  ingestion/  preprocessing/  ml_inference/  event_sink/
  pipelines/                    # Dagster assets, jobs, schedules
  frontend/
  infra/
    migrations/                 # golang-migrate SQL (PostGIS)
    init/minio-init.sh  init/kafka-init.sh
    nginx/nginx.conf
    grafana/  prometheus/
    terraform/                  # aws: s3, ec2, iam, cloudfront, ssm, ecr, budgets, athena/glue, scheduler
  scripts/upload_training_data.py  scripts/deploy.sh
  .github/workflows/            # ci.yml, release.yml, deploy.yml
```

---

## 14. CI/CD (GitHub Actions)

1. **ci.yml** (every PR): `go vet` + `go test` + build, `pytest` for each Python package, `oxlint` + build for the frontend, contract tests, then the **compose e2e smoke test on MinIO** using a **fixture scene** (a small real EMIT + Landsat crop in `infra/fixtures/`, so CI never calls NASA).
2. **release.yml** (tag): build **multi-arch (amd64 + arm64)** images and push them to **ECR** (or GHCR, which is free).
3. **deploy.yml** (manual): `aws ssm send-command` on the instance runs `scripts/deploy.sh`: pull secrets → `.env`, run `docker compose pull && up -d`, run migrations, check health. GitHub authenticates to AWS with **OIDC** (no stored keys).

---

## 15. AWS deployment (Terraform)

| Resource | Setting |
|---|---|
| Region | us-west-2 |
| S3 | `hyperspectral-<name>-prod`: versioning on `models/`, lifecycle per §6.1, CORS for the CloudFront domain, public access blocked |
| EC2 | **t4g.large** (2 vCPU, 8 GB, ARM; or t3.large on x86), 50 GB gp3, Amazon Linux 2023 + Docker, instance role, no public SSH |
| CloudFront | origin 1: S3 (frontend build). Origin 2: EC2 for `/api/*` (SSE needs caching off). The default `*.cloudfront.net` HTTPS needs no domain. |
| ECR | one repository per service. Or use GHCR and skip ECR. |
| SSM | Parameter Store secrets + Session Manager |
| Athena + Glue | database + tables over `gold/tables/` and `bronze/events/` |
| EventBridge Scheduler | **stop the instance at night and start it in the morning** to stretch credits |
| Budgets | alerts at $1, 25 %, 50 % and 80 % of the credit, by email |

**Rough monthly cost, us-west-2 on-demand** (estimate only; check with the AWS Pricing Calculator):

| Item | 24/7 | ~10 h/day |
|---|---|---|
| t4g.large | ~$49 | ~$20 |
| 50 GB gp3 EBS | ~$4 | ~$4 |
| Public IPv4 | ~$3.6 | ~$3.6 |
| S3 (tens of GB) + requests | ~$1–3 | ~$1–3 |
| CloudFront, CloudWatch Logs (short retention), Athena (MB scans) | ~$0–3 | ~$0–3 |
| **Total** | **~$60** | **~$30** |

What we avoid on credits: MSK (hundreds of $/month), NAT Gateway (~$32/month), and ECS/EKS, RDS and ElastiCache. These are listed as the scale-up path below.

**Scale-up path (no code changes, only infra):**

| When | Move |
|---|---|
| Need HA DB / backups managed | Postgres → RDS (PostGIS supported) |
| Many concurrent jobs | services → ECS Fargate, autoscaled on Kafka lag |
| Kafka beyond one broker | MSK, or a 3-broker KRaft cluster |
| Many users | Redis → ElastiCache, gateway behind an ALB |

**If credits end:** copy the bucket with `rclone`, move compose to any VM with S3-compatible storage, change `.env`. Done.

---

## 16. Phases (two people; Aaron = A, Ananthanarayanan = N)

| Phase | Work | Owner | Done when | ~Weeks |
|---|---|---|---|---|
| **P0 Boot** | Fix B1–B6, B11, B12, B16. Merge ingestion. Add healthchecks, init containers, KRaft Kafka, PostGIS image. | A | `docker compose up` on the laptop gives all services healthy, and register/login work | 1 |
| **P1 Storage + lake** | `hs_common.storage`, Go dual client, one bucket + Bronze/Silver/Gold, minio-init | A (+N for preprocessing paths) | The same images pass a storage test on MinIO and on S3 | 0.5 |
| **P2 Contracts + control plane** | JSON Schemas, outbox, orchestrator rewrite, migrations, Redis keys, SSE, watchdog, DLQ | A | A fake job through stub workers shows every stage live in the UI, and failures show their reason | 1.5 |
| **P3 Real ingestion + catalog** | EMIT search/lock/stage/GLT ortho/crop/mask, Landsat STAC windowed reads, PostGIS catalog, DQ checks | N + A | A Central Valley bbox gives a georeferenced `raw.zarr` and `lst` window, catalogued | 2 |
| **P4 Preprocessing outputs** | `reflectance.zarr`, EMIT mask, georef, JSON, thermal worker, DQ | N | 57 existing + new contract tests pass | 1 |
| **P5 Features + training data** (parallel from P0) | §11.2 package + tests, PROSAIL gen, mineral synthesis, thermal labels, Dagster assets, upload to Gold | A | Feature tests pass, datasets versioned with DQ reports | 1.5 |
| **P6 HydraNet** | model, training, baselines, ablations, model card, registry/promotion | A | §11.5 gates met, or a documented decision | 1.5 |
| **P7 Inference service** | tiles, masks, COG + PNG + STAC + manifest, spec-hash check | A | Fixture scene → correct georeferenced layers in < 10 s | 1 |
| **P8 Frontend** | §10.7 | A | Draw → run 3 tasks → live timeline → real overlays → pixel inspect → download | 1.5 |
| **P9 Data platform extras** | event-sink, compaction, Gold tables, drift report, Grafana dashboards, pg_backup | A + N | Dashboards show the job funnel and stage latency, and DuckDB/Athena queries work | 1 |
| **P10 CI/CD + AWS** | GitHub Actions, multi-arch images, Terraform, SSM deploy, budgets, scheduler | A | A public CloudFront URL works end to end on S3, from env changes only | 1.5 |
| **P11 Hardening** | load test (10 concurrent jobs), chaos (kill a worker mid-job), security review, docs | Both | Recovers or fails cleanly with a visible error, and the runbook is written | 1 |

Critical path: P0 → P1 → P2 → P3 → P4 → P7 → P8 → P10. **P5 and P6 (the model) need only the CSVs and run in parallel from week 1.** Total: about 10–12 weeks for two people, part-time-realistic.

---

## 17. Open items to confirm

1. Get `training_set.npz`, `dataset.npz`, `features.py` and `synth.py` from the mineral owner, or confirm regeneration (the default).
2. Confirm the duplicated USGS library files (finding 8).
3. Thermal v1 pseudo-labels + anomaly: OK? Can we get pixel coordinates for the thermal CSV?
4. Allowed map region: keep the Central Valley lock (enables pre-staging), or go global?
5. Which AWS offer: a **standard account with credits** works with this plan. **AWS Academy Learner Lab** stops instances at session end and limits IAM and Terraform, so it is fine for testing but not for hosting.
6. Dagster (default) or Airflow (heavier, more recognisable on a CV)?
