# HydraNet Maps

Draw a box on the map and get **agriculture, mineral and thermal maps** of it,
made by **one neural network (HydraNet, three heads, ONNX)** from NASA EMIT
hyperspectral and Landsat 8/9 thermal imagery. Every map is trained, verified
and continuously re-checked to be the **same map the classical method draws**:

| Map | Classical method HydraNet reproduces | Agreement on the deployed ONNX file |
|---|---|---|
| Minerals | SAM class (USGS library, 0.20 rad → unknown) + SMA/FCLS abundances + quality | on 2 **real EMIT scenes never trained on**: class 100 % / 99.99 %, abundance error 0.003 / 0.0015, quality error 0.004 / 0.002 |
| Agriculture | PROSAIL look-up-table inversion (k = 20) | Cab / Cw / LAI within 1.7 / 1.5 / 3.4 % of range (real Fresno job: 1.8 / 2.1 / 1.7 %) |
| Thermal | `ThermalAnomalyDetector`: global/local z, UHI p80/p20, hotspots p95 | ≥ 99.6 % overall, every map ≥ 98.8 % on each of **6 real Landsat scenes** |

Model v1: lightweight MLP, 502k parameters, 2.06 MB ONNX, 530 mineral inputs
(continuum-removed + raw reflectance), trained on USGS mixtures (with dry
vegetation and soil moisture) plus 200k real EMIT pixels from 5 scenes cleaned
by the production preprocessing (`training/README.md`).

Full numbers: `training/artifacts/models/v1/{model_card.json,map_verification.md}`
and the **Platform** page of the app.

**On Hugging Face:** the model [aaronr43210/HydraNet-Hyperspectral](https://huggingface.co/aaronr43210/HydraNet-Hyperspectral)
and a no-sign-in demo of real maps, [HydraNet Maps](https://huggingface.co/spaces/aaronr43210/HydraNet-Maps)
(publish/update: [docs/SETUP_HUGGINGFACE.md](docs/SETUP_HUGGINGFACE.md)).

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
| `tests/` | end-to-end API test, attack tests, browser test, k6 load test, a fake Google sign-in for tests (`mockoidc/`) |
| `docs/` | project document, AWS and Hugging Face setup guides, report and demo builders |

## Run it locally

Needs Docker Desktop with ≥ 8 GB memory.

```bash
cp .env.example .env              # fill the secrets (openssl rand -hex 32), MinIO keys, Google client
# optional: EARTHDATA_TOKEN for real EMIT (Landsat needs no account)
docker compose --profile local up -d --build
open http://localhost:3000        # Sign in with Google (ADMIN_EMAIL becomes admin)
```

Sign-in is **Google only**. Locally either use a real Google OAuth client (add
`http://localhost:3000/api/auth/google/callback` as a redirect URI, put its id/secret in `.env`
and your address in `ALLOWED_EMAILS`), or the built-in fake Google for testing:
`OIDC_ISSUER=http://mock-oidc:9998`, `GOOGLE_CLIENT_ID=ci-client`, `GOOGLE_CLIENT_SECRET=ci-secret`,
`ALLOWED_EMAIL_DOMAINS=example.com`, then `docker compose --profile local --profile test up -d`.
A production gateway refuses the fake one.

The first start applies the database migrations, creates the bucket and
promotes the trained model in `training/artifacts/models/v1` (train it first:
`training/README.md`). Add `--profile obs` for Prometheus (127.0.0.1:9090) and
Grafana (127.0.0.1:3002), `--profile batch` for Dagster (localhost:3001).

**EMIT coverage on the map:** the init indexes every EMIT scene over the region
(NASA CMR, public) into PostGIS, Dagster refreshes it daily, and the map dims
everything without a usable scene for the chosen dates. Refresh by hand with
`docker compose run --rm tools python scripts/refresh_catalog.py`. The same
catalog lets jobs keep working while NASA's search service (CMR) is down.

Scale workers (up to each topic's partitions):
`docker compose --profile local up -d --scale ml_inference=4 --scale preprocessing=4 --scale ingestion=4`

## Tests

```bash
pytest libs/hydra/tests libs/hs_common/tests          # parity with the owners' reference code, ONNX runtime, EMIT ortho
(cd preprocessing && pytest tests)                     # 46 tests
go vet ./... && go test ./...                          # incl. Go structs vs message schemas
# the next ones sign in through the fake Google: run the stack with --profile test (see above)
python tests/e2e.py --base http://localhost:3000 --jobs 3 --real-thermal
python tests/security.py --base http://localhost:3000 --env .env   # attacks that must all be blocked
docker run --rm --network host -v $PWD/tests:/t mcr.microsoft.com/playwright/python:v1.48.0-jammy \
  sh -c "pip install -q playwright==1.48.0 && python /t/ui_smoke.py http://localhost:3000 /tmp"
# 8 screen sizes, touch and mouse: layout, touch drawing, overflow
docker run --rm --network host -v $PWD/tests:/t mcr.microsoft.com/playwright/python:v1.48.0-jammy \
  sh -c "pip install -q playwright==1.48.0 && python /t/ui_responsive.py http://localhost:3000"
docker run --rm --network hydranet_default -v $PWD/tests/load:/s grafana/k6:0.54.0 run /s/k6.js
```

Measured on one laptop (all services in Docker, 12 CPU, 8 GB):

| Load test | Result |
|---|---|
| 300 concurrent users (sign in once, submit, poll, view maps) | 0 failed of 55,225 requests; p95: API 5.4 ms, submit 8.6 ms, status 2.3 ms, layers 7.6 ms |
| 60 users, every job a new area, 2 replicas per worker | 1.7 jobs/s (all three maps), median 25 s |
| same, 4 replicas per worker | **3.8 jobs/s**, median 3 s: scales horizontally |

## Security

Google-only sign-in (no passwords stored; PKCE, single-use state and nonce, an allow list),
short-lived JWTs (15 min, in memory only) + rotating HttpOnly refresh cookies with theft
detection, per-IP limits that cannot be dodged with forged headers, single-use stream tickets, strict CSP and security headers, least-privilege
containers, audit log, and dependency/secret/image scanning in CI. Threat model,
operations (key rotation, incident response) and trade-offs: **[SECURITY.md](SECURITY.md)**.

## Deploy to AWS (credits-friendly)

Step by step, including what to get from AWS, Google and NASA: **[docs/SETUP_AWS.md](docs/SETUP_AWS.md)**.

`infra/terraform`: one EC2 m7i-flex.large (Free-plan type; fixed Elastic IP, swap, runs 08:00–20:00) running this compose stack,
S3 data lake, CloudFront on your domain (ACM), HTTPS + secret header from CloudFront to the server,
ECR, SSM secrets, daily Postgres and disk backups, budget alerts, optional WAF, GitHub OIDC; state in a
private S3 bucket. Then `release.yml` (multi-arch images on a `v*` tag) and `deploy.yml` (through SSM,
approved in the `production` environment). About $44/month (less on weekdays-only hours); no domain needed (`*.cloudfront.net`).

## Known limitations (honest list)

* **Real-imagery coverage:** minerals are validated on 7 real EMIT scenes in California and agriculture on
  real Central Valley farmland; other regions, seasons and sensors are not yet. Add scenes with
  `training/harvest_emit.py` and retrain.
* Labels are the classical methods' outputs: HydraNet reproduces SAM/SMA/PROSAIL/the thermal rules; it does
  not measure anything they cannot (ground truth is the path to exceed them).
* Thermal: the hardest real scene (Tulare farms, many pixels at the p95 hotspot threshold) agrees 98.8 % on
  hotspots, the others ≥ 99.9 %; `verify_maps.py` gates every scene at ≥ 98 %.
* Thermal labels are the detector's rules (the dataset has no labels); NASA FIRMS active-fire points are the next step.
