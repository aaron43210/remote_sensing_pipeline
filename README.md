# HydraNet Maps

Draw a box on the map and get **agriculture, mineral and thermal maps** of it,
made by **one neural network (HydraNet, three heads, ONNX)** from NASA EMIT
hyperspectral and Landsat 8/9 thermal imagery. Every map is trained, verified
and continuously re-checked to be the **same map the classical method draws**:

| Map | Classical method HydraNet reproduces | Agreement on the deployed ONNX file |
|---|---|---|
| Minerals | SAM class (USGS library, 0.20 rad → unknown) + SMA/FCLS abundances + quality | class 99.96 % (scenes), **99.85 % on 2,000 real EMIT pixels** |
| Agriculture | PROSAIL look-up-table inversion (k = 20) | Cab / Cw / LAI within 1.6 / 1.4 / 3.4 % of range |
| Thermal | `ThermalAnomalyDetector`: global/local z, UHI p80/p20, hotspots p95 | ≥ 99.2 % on **6 real Landsat scenes** |

Full numbers: `training/artifacts/models/v1/{model_card.json,map_verification.md}`
and the **Platform** page of the app.

## Architecture

```
Browser ─► nginx/CloudFront ─► api_gateway (Go, N replicas) ──► Postgres + PostGIS (truth)
   ▲ SSE                          │ outbox ─► Kafka                 Redis (live status, locks, limits)
   │                              ▼
   │        ingestion ─► preprocessing ─► ml_inference (ONNX Runtime) ─► ml.results
   │        (EMIT/Landsat)  (Dask)          + shadow check vs classical      │
   └─────────── Redis pub/sub ◄── orchestrator (Go, state machine) ◄────────┘
                                   event_sink ─► data lake (bronze/silver/gold Parquet)
Object storage: MinIO locally, Amazon S3 (or any S3-compatible) in deployment. Same code.
Batch (Dagster): imagery catalog, pre-staging, compaction, Gold tables, agreement report, backups.
```

| Folder | What |
|---|---|
| `api_gateway/`, `orchestrator/`, `pkg/` | Go control plane: API, auth, admission control, outbox, state machine, watchdog |
| `ingestion/` | EMIT (CMR search, Earthdata download, GLT orthorectification, cloud mask) and Landsat (STAC, window-only COG reads) |
| `preprocessing/` | the Dask spectral cleaning pipeline (owner: Ananthanarayanan) + Landsat calibration, wired to Kafka by `service.py` / `thermal_service.py` |
| `ml_inference/` | HydraNet serving: features → ONNX → COG + PNG layers, STAC item, shadow verification |
| `libs/hydra/` | the one feature spec, the classical methods (teachers), ONNX runtime, scene → maps |
| `libs/hs_common/` | storage, Kafka (retries, DLQ, idempotency), message contracts, status, geo outputs |
| `training/` | data generation (teacher-labelled), training, ONNX export, gates, map verification |
| `frontend/` | React + Leaflet SPA |
| `pipelines/` | Dagster batch assets |
| `infra/` | MinIO (built from source), Kafka topics, Postgres init, nginx, Prometheus, Grafana, **Terraform (AWS)** |
| `tests/` | end-to-end API test, browser test, k6 load test |

## Run it locally

Needs Docker Desktop with ≥ 8 GB memory.

```bash
cp .env.example .env              # fill the secrets (openssl rand -hex 32), MinIO keys, admin
# optional: EARTHDATA_TOKEN for real EMIT (Landsat needs no account)
docker compose --profile local up -d --build
open http://localhost:3000        # sign in as ADMIN_EMAIL, or register
```

The first start applies the database migrations, creates the bucket and
promotes the trained model in `training/artifacts/models/v1` (train it first:
`training/README.md`). Add `--profile obs` for Prometheus (127.0.0.1:9090) and
Grafana (127.0.0.1:3002), `--profile batch` for Dagster (localhost:3001).

Scale workers (up to each topic's partitions):
`docker compose --profile local up -d --scale ml_inference=4 --scale preprocessing=4 --scale ingestion=4`

## Tests

```bash
pytest libs/hydra/tests libs/hs_common/tests          # parity with the owners' reference code, ONNX runtime, EMIT ortho
(cd preprocessing && pytest tests)                     # 54 + 1 skipped
go vet ./... && go test ./...                          # incl. Go structs vs message schemas
python tests/e2e.py --base http://localhost:3000 --jobs 3 --real-thermal
python tests/security.py --base http://localhost:3000 --env .env   # 60+ attacks that must all be blocked
docker run --rm --network host -v $PWD/tests:/t mcr.microsoft.com/playwright/python:v1.48.0-jammy \
  sh -c "pip install -q playwright==1.48.0 && python /t/ui_smoke.py http://localhost:3000 /tmp"
docker run --rm --network hydranet_default -v $PWD/tests/load:/s grafana/k6:0.54.0 run /s/k6.js
```

Measured on one laptop (all services in Docker, 12 CPU, 8 GB):

| Load test | Result |
|---|---|
| 300 concurrent users (sign in once, submit, poll, view maps) | 0 failed of 55,225 requests; p95: API 5.4 ms, submit 8.6 ms, status 2.3 ms, layers 7.6 ms |
| 60 users, every job a new area, 2 replicas per worker | 1.7 jobs/s (all three maps), median 25 s |
| same, 4 replicas per worker | **3.8 jobs/s**, median 3 s: scales horizontally |

## Security

Short-lived JWTs (15 min, in memory only) + rotating HttpOnly refresh cookies with theft
detection, per-IP and per-account brute-force limits that cannot be dodged with forged
headers, single-use stream tickets, strict CSP and security headers, least-privilege
containers, audit log, and dependency/secret/image scanning in CI. Threat model,
operations (key rotation, incident response) and trade-offs: **[SECURITY.md](SECURITY.md)**.

## Deploy to AWS (credits-friendly)

`infra/terraform`: one EC2 t4g.large running this compose stack, S3 data lake,
CloudFront (HTTPS), ECR, SSM secrets, Athena/Glue, budget alerts, night-time
stop/start, GitHub OIDC. Then `release.yml` (multi-arch images) and
`deploy.yml` (SSM). `S3_ENDPOINT_URL` empty = AWS; nothing else changes.
Moving to another S3-compatible provider later: endpoint + keys in `.env`.

## Known limitations (honest list)

* **Mineral abundances on real EMIT pixels** differ from SMA by MAE 0.078 (gate 0.06), measured on the
  2,000 pseudo-labelled real rows; the network sees the 275 continuum-removed features, SMA sees raw
  reflectance. v1 was promoted with `--force` (recorded in the registry and on the Platform page). Class maps
  are unaffected (99.85 %). Next step: validate on real scenes (needs an Earthdata login) and, if confirmed,
  add reflectance-level inputs.
* EMIT download/orthorectification is unit-tested on a file with EMIT's exact layout, not yet on a real
  granule (needs Earthdata credentials).
* Thermal labels are the detector's rules (the dataset has no labels); FIRMS active-fire points are the v2 path.
