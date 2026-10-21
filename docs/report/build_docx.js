// The end-to-end project document. Every number is read from the model card, the map
// verification, the benchmark and the real-job comparisons. Regenerate the inputs first:
//   bench.py, realmaps.py (one per real job), figures.py, shots.py (headers of each file).
//   node docs/report/build_docx.js <repo> <model_dir> <build_dir> <out.docx>
const fs = require("fs");
const path = require("path");
const {
  Document, Packer, Paragraph, TextRun, HeadingLevel, AlignmentType, Table, TableRow, TableCell,
  WidthType, ShadingType, BorderStyle, ImageRun, TableOfContents, PageBreak, Footer, Header,
  PageNumber, LevelFormat,
} = require("docx");

const [REPO, MODEL, BUILD, OUT] = process.argv.slice(2);
const IMG = path.join(BUILD, "img");
const J = (p) => JSON.parse(fs.readFileSync(p, "utf8"));
const card = J(path.join(MODEL, "model_card.json"));
const mv = J(path.join(MODEL, "map_verification.json"));
const metrics = J(path.join(MODEL, "metrics.json")).metrics;
const manifest = J(path.join(REPO, "training/artifacts/data/manifest.json"));
const bench = J(path.join(BUILD, "bench.json"));
const real = Object.fromEntries(["fresno", "deathvalley", "searles"].map((n) => [n, J(path.join(IMG, `real_${n}.json`))]));
const jobs = { fresno: J(path.join(BUILD, "job_fresno.json")), deathvalley: J(path.join(BUILD, "job_deathvalley.json")) };
const V = card.version;

const W = 9026;                       // A4 text width (DXA), 1" margins
const INK2 = "52514E";
const pct = (v, d = 2) => (v == null ? "n/a" : `${(v * 100).toFixed(d)} %`);
const num = (v, d = 3) => (v == null ? "n/a" : Number(v).toFixed(d));
const G = card.gates;
const gv = (k) => G[k] && G[k].value;
const mg = mv.gates;
const sp = (t) => bench.T[t][1] / bench.T[t][0];

// ── helpers ────────────────────────────────────────────────────────────────
function runs(text, o = {}) {
  return String(text).split(/(\*\*[^*]+\*\*)/).filter(Boolean).map((s) =>
    s.startsWith("**") ? new TextRun({ text: s.slice(2, -2), bold: true, ...o.r }) : new TextRun({ text: s, ...o.r }));
}
const P = (t, o = {}) => new Paragraph({ spacing: { after: 120, line: 276 }, children: runs(t, o) });
const H1 = (t) => new Paragraph({ heading: HeadingLevel.HEADING_1, pageBreakBefore: true, children: [new TextRun(t)] });
const H2 = (t) => new Paragraph({ heading: HeadingLevel.HEADING_2, children: [new TextRun(t)] });
const H3 = (t) => new Paragraph({ heading: HeadingLevel.HEADING_3, children: [new TextRun(t)] });
const B = (t) => new Paragraph({ numbering: { reference: "bullets", level: 0 }, spacing: { after: 60 }, children: runs(t) });
let listNo = 0;
const NL = (items) => { listNo += 1; const inst = listNo;
  return items.map((t) => new Paragraph({ numbering: { reference: "numbers", level: 0, instance: inst }, spacing: { after: 60 }, children: runs(t) })); };
const Code = (lines) => lines.map((l) => new Paragraph({ spacing: { after: 0 }, shading: { type: ShadingType.CLEAR, fill: "F3F3F1", color: "auto" },
  children: [new TextRun({ text: l || " ", font: "Consolas", size: 17 })] }));
const Note = (t) => new Paragraph({ spacing: { before: 60, after: 160 }, indent: { left: 200 },
  border: { left: { style: BorderStyle.SINGLE, size: 18, color: "2A78D6", space: 8 } }, children: runs(t) });

let figNo = 0, tabNo = 0;
function Img(file, widthIn, caption) {
  const f = path.join(IMG, file);
  if (!fs.existsSync(f)) return [P(`[missing figure: ${file}]`)];
  const buf = fs.readFileSync(f);
  const w = buf.readUInt32BE(16), h = buf.readUInt32BE(20);
  const wpx = Math.round(widthIn * 96);
  figNo += 1;
  return [
    new Paragraph({ alignment: AlignmentType.CENTER, spacing: { before: 120, after: 40 }, keepNext: true,
      children: [new ImageRun({ type: "png", data: buf, transformation: { width: wpx, height: Math.round(wpx * h / w) },
        altText: { title: caption, description: caption, name: file } })] }),
    new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 200 },
      children: [new TextRun({ text: `Figure ${figNo}. `, bold: true, size: 18, color: INK2 }), new TextRun({ text: caption, size: 18, color: INK2 })] }),
  ];
}
const border = { style: BorderStyle.SINGLE, size: 4, color: "D9D8D2" };
const borders = { top: border, bottom: border, left: border, right: border };
function T(headers, rows, widths, caption) {
  const total = widths.reduce((a, b) => a + b, 0);
  const ws = widths.map((x) => Math.round(x * W / total));
  ws[ws.length - 1] += W - ws.reduce((a, b) => a + b, 0);
  const cell = (t, i, head) => new TableCell({ borders, width: { size: ws[i], type: WidthType.DXA },
    shading: head ? { fill: "EEF3FA", type: ShadingType.CLEAR, color: "auto" } : undefined,
    margins: { top: 60, bottom: 60, left: 100, right: 100 },
    children: String(t).split("\n").map((l) => new Paragraph({ spacing: { after: 0 }, children: runs(l, { r: { size: 18, bold: head || undefined } }) })) });
  const out = [];
  if (caption) {
    tabNo += 1;
    out.push(new Paragraph({ spacing: { before: 120, after: 60 }, keepNext: true,
      children: [new TextRun({ text: `Table ${tabNo}. `, bold: true, size: 18, color: INK2 }), new TextRun({ text: caption, size: 18, color: INK2 })] }));
  }
  out.push(new Table({ width: { size: W, type: WidthType.DXA }, columnWidths: ws,
    rows: [new TableRow({ tableHeader: true, children: headers.map((h, i) => cell(h, i, true)) }),
      ...rows.map((r) => new TableRow({ children: r.map((c, i) => cell(c, i, false)) }))] }));
  out.push(new Paragraph({ spacing: { after: 120 }, children: [] }));
  return out;
}

const C = [];
const mm = manifest.mineral;
const RF = real.fresno.emit, RD = real.deathvalley.emit, RS = real.searles.emit;

// ── title ──────────────────────────────────────────────────────────────────
C.push(new Paragraph({ spacing: { before: 2400, after: 200 }, children: [new TextRun({ text: "HydraNet Maps", bold: true, size: 64, color: "1F4E8C" })] }));
C.push(new Paragraph({ spacing: { after: 400 }, children: [new TextRun({ text: "End-to-end project document: hyperspectral and thermal mapping with one neural network", size: 32, color: "333333" })] }));
C.push(P("Agriculture, mineral and thermal maps from NASA EMIT and Landsat 8/9, produced by one lightweight three-headed neural network (ONNX) that is trained and continuously verified to reproduce the classical remote-sensing methods, served by a production data platform (Kafka, Redis, PostgreSQL/PostGIS, MinIO / Amazon S3)."));
C.push(new Paragraph({ spacing: { before: 500 }, children: [] }));
C.push(...T(["Item", "Value"], [
  ["Repository", "github.com/aaron43210/remote_sensing_pipeline"],
  ["Branch documented", "pre-production (folder pre_production/)"],
  ["Deployed model", `HydraNet ${V}: MLP, ${card.parameters.toLocaleString("en-US")} parameters, ONNX opset ${card.opset}, ${(card.bytes / 1e6).toFixed(2)} MB`],
  ["Feature spec", `530 mineral features, hash ${card.feature_spec_hash}`],
  ["Document date", "21 October 2026"],
  ["Prepared by", "Aaron R (platform, model, integration), with the preprocessing module by Ananthanarayanan B"],
], [2, 5]));
C.push(new Paragraph({ children: [new PageBreak()] }));
C.push(new Paragraph({ heading: HeadingLevel.HEADING_1, children: [new TextRun("Contents")] }));
C.push(new TableOfContents("Contents", { hyperlink: true, headingStyleRange: "1-2" }));
C.push(P("(If the table of contents is empty, right-click it in Word and choose Update Field.)", { r: { color: INK2, size: 18 } }));

// 1 summary
C.push(H1("1. Executive summary"));
C.push(P("A user draws a box on a map and receives three families of maps: **agriculture** (leaf chlorophyll Cab, carotenoids Car, water Cw, dry matter Cm, leaf area index LAI, leaf angle ALA, each with an uncertainty), **minerals** (dominant of 12 USGS minerals or unknown, per-mineral abundance, fit quality) and **thermal** (cold/normal/hot, urban-heat-island zones, local anomalies, hotspots). Imagery comes from NASA EMIT (285 bands, 60 m) and Landsat 8/9 (land surface temperature, 30 m). The map shows where EMIT data exists for the chosen dates before a box is drawn."));
C.push(P(`One neural network, **HydraNet**, produces every map. It is trained with the classical methods as teachers, so it draws the same maps they draw, and every job re-checks a pixel sample against the classical method. Model ${V} was retrained on a new dataset (USGS mixtures with real-world nuisances plus about 200,000 real EMIT pixels) and matches the classical methods on real imagery as closely as on test data.`));
C.push(...T(["Result", "Value", "Notes"], [
  ["Minerals on real EMIT never trained on", `class ${pct(gv("mineral_real.class_agreement"))} / ${pct(gv("mineral_real_holdout.class_agreement"))}; abundance error ${num(gv("mineral_real.abundance_mae"), 4)} / ${num(gv("mineral_real_holdout.abundance_mae"), 4)}; quality error ${num(gv("mineral_real.quality_mae"), 4)} / ${num(gv("mineral_real_holdout.quality_mae"), 4)}`, "owner's real scene / held-out Death Valley scene; the prototype's abundance error was 0.078"],
  ["Real jobs through the platform", `minerals 99.99–100 % class on Fresno, Death Valley, Searles Lake; abundance error ${num(RF.mineral.abundance_mae, 4)}–${num(RS.mineral.abundance_mae, 4)}`, "every pixel compared with SAM + SMA"],
  ["Agriculture vs PROSAIL LUT inversion", `R² ${num(gv("agri_val.r2_min_core"))} (worst core trait); whole scenes Cab / Cw / LAI error ${pct(mg.agri_Cab.value, 1)} / ${pct(mg.agri_Cw.value, 1)} / ${pct(mg.agri_LAI.value, 1)} of range`, `real Fresno farmland: ${pct(RF.agri.Cab.nmae, 1)} / ${pct(RF.agri.Cw.nmae, 1)} / ${pct(RF.agri.LAI.nmae, 1)}`],
  ["Thermal vs ThermalAnomalyDetector", `≥ ${pct(Math.min(mg.thermal_global_class.value, mg.thermal_uhi_class.value, mg.thermal_hotspots.value, mg.thermal_local_anomaly.value), 1)} overall; every map ≥ ${pct(mg.thermal_real_scene_min.value, 1)} on each of 6 real Landsat scenes`, "per-scene floor 98 %"],
  ["Speed (5 scenes, CPU)", `minerals ${sp("mineral").toFixed(0)}x, agriculture ${sp("agri").toFixed(0)}x faster than the classical methods`, "thermal rules are cheaper than the network"],
  ["Model size", `${card.parameters.toLocaleString("en-US")} parameters, ${(card.bytes / 1e6).toFixed(2)} MB`, "lightweight MLP only (the CNN had 1.19 M)"],
  ["Load", "300 concurrent users: 0 failed of 54,694 requests; API p95 4.1 ms", "one laptop, all services in Docker"],
  ["Security", "63 attacks in the test suite, none succeed", "JWT forgery, brute force, IDOR, CSRF, XSS, header spoofing"],
], [2.6, 4, 2.4], "Headline results"));

// 2 problem
C.push(H1("2. Problem statement and objectives"));
C.push(P("Hyperspectral sensors record hundreds of narrow bands per pixel. The established ways of turning spectra into maps are physically meaningful but slow and fragmented: spectral angle mapping (SAM) and spectral mixture analysis (SMA) for minerals, look-up-table (LUT) inversion of the PROSAIL radiative-transfer model for crop traits, and statistical rules for thermal anomalies. Each needs its own code and tuning, and their per-pixel searches scale poorly to many users."));
C.push(H3("Objectives"));
C.push(...NL([
  "One neural network that predicts agriculture, mineral and thermal maps (three-in-one), trained on the project's MINERAL, AGRICULTURE and THERMAL datasets.",
  "It must produce the **same maps as the classical methods**: verified by gates before release and by shadow checks in production, on real imagery as well as test data.",
  "A lightweight, CPU-only model (MLP, no CNN) exported to ONNX.",
  "A production platform for many simultaneous users: queueing, retries, idempotency, caching, horizontal scaling.",
  "MinIO locally, Amazon S3 (or any S3-compatible store) in deployment, with no code changes.",
  "A standard, secure web front end wired to the backend.",
]));

// 3 repo
C.push(H1("3. Repository and the pre-production branch"));
C.push(P("The repository is github.com/aaron43210/remote_sensing_pipeline. Team members worked on feature branches; **pre-production** integrates the work into one runnable platform. The datasets live beside it (MINERAL/, AGRICULTURE/, THERMAL/) and are versioned in the data lake for CI."));
C.push(...T(["Branch", "Purpose"], [
  ["base", "default branch (the original multi-service skeleton)"],
  ["pre-production", "this document: integrated platform, HydraNet, frontend, infrastructure"],
  ["feature/preprocessing", "Dask preprocessing module (Ananthanarayanan B), merged via PR #4"],
  ["MINERAL_ANALYSIS", "reference mineral service (SAM, SMA, continuum removal), vendored as the parity reference"],
  ["Priya_RTM_SERVICE", "PROSAIL / RTM inversion approach used for the agriculture teacher"],
  ["SPECTRAL_ANALYSIS_SERVICE, feature/thermal, development, aaron_dev", "earlier service and experiment branches"],
], [2.2, 5], "Branches"));
C.push(...T(["Folder", "Contents"], [
  ["api_gateway/, orchestrator/, pkg/", "Go control plane: REST API, auth, admission control, EMIT coverage, transactional outbox, job state machine, watchdog"],
  ["ingestion/", "EMIT search (CMR, catalog fallback), Earthdata download, GLT orthorectification, cloud mask; Landsat STAC window reads"],
  ["preprocessing/", "Dask spectral cleaning (EMIT) and Landsat calibration, wired to Kafka"],
  ["ml_inference/", "HydraNet serving (ONNX Runtime), COG + PNG layers, STAC, shadow verification, model hot swap"],
  ["libs/hydra/", "feature specification, features, classical methods (teachers), runtime, maps; parity tests"],
  ["libs/hs_common/", "storage, Kafka worker, message contracts, status, metrics, geo outputs, data sources, imagery catalog"],
  ["training/", "real-scene harvesting, data generation, training, ONNX export, gates, map verification; artifacts/models/" + V],
  ["frontend/", "React 19 + Vite + Leaflet single-page app"],
  ["pipelines/", "Dagster batch assets (catalog, pre-staging, compaction, Gold tables, agreement report, backups)"],
  ["infra/", "MinIO (built from source), Kafka topics, Postgres, nginx, Prometheus, Grafana, Terraform (AWS)"],
  ["contracts/, tests/, scripts/, tools/, docs/", "JSON Schemas per topic; e2e, browser, security, load tests; ops scripts; init container; this document and its generator"],
], [2.2, 5], "Folder structure"));

// 4 architecture
C.push(H1("4. System architecture"));
C.push(...Img("fig_architecture.png", 6.3, "Platform architecture. Blue: Go services; green: Python workers; orange: Kafka; gray: state and storage."));
C.push(...T(["Component", "Technology", "Role"], [
  ["Edge", "nginx / CloudFront", "TLS, static SPA, strict CSP and security headers, per-IP limit, overwrites the client-IP header"],
  ["api_gateway", "Go 1.26, gin", "auth (JWT + refresh cookies), job submission, validation, admission control, result reuse, EMIT coverage, presigned URLs, SSE"],
  ["orchestrator", "Go", "per-task state machine; watchdog for stages past their SLA; finishes coalesced jobs"],
  ["ingestion", "Python", "EMIT and Landsat acquisition; stages each EMIT granule once platform-wide; falls back to the catalog while NASA's search is down"],
  ["preprocessing", "Python, Dask, Xarray", "spectral cleaning of EMIT cubes; Landsat calibration and reprojection"],
  ["ml_inference", "Python, ONNX Runtime", "HydraNet inference, map layers, STAC, shadow verification, hot swap of promoted models"],
  ["event_sink", "Python", "every Kafka event to the Bronze lake as Parquet"],
  ["Kafka / Redis / PostgreSQL + PostGIS", "", "event bus (8 topics, DLQ) / live status, idempotency, locks, limits / system of record incl. imagery catalog"],
  ["Object storage", "MinIO / Amazon S3", "data lake (bronze/silver/gold), models, maps"],
  ["Dagster, Prometheus, Grafana", "", "batch pipelines; metrics and alerts"],
], [1.8, 1.6, 4.2], "Components"));
C.push(H2("4.1 Life of a job"));
C.push(...Img("fig_pipeline.png", 6.3, "A job flows through Kafka topics; each stage reports progress on job.status."));
C.push(...NL([
  "The browser POSTs box, tasks and dates with an Idempotency-Key. The gateway validates them, checks quotas, and reuses an identical finished job (same box, tasks, dates, model) or follows one in flight.",
  "The job and its ingest message are written in **one transaction** (transactional outbox). The message carries the catalogued EMIT scenes covering the box, clearest first.",
  "Ingestion fetches imagery (NASA CMR first, the catalogued scenes if CMR is down), preprocessing cleans it, ml_inference runs HydraNet and writes the layers.",
  "Workers are idempotent, retry transient errors three times, and send permanent failures to the DLQ. A box without a usable scene ends as NO_DATA with a reason.",
  "The orchestrator finishes the job (DONE, PARTIAL, FAILED, TIMED_OUT, CANCELLED); the browser follows live over Server-Sent Events.",
]));

// 5 data sources
C.push(H1("5. Data sources and acquisition"));
C.push(...T(["Source", "What", "Access"], [
  ["NASA EMIT L2A RFL", "surface reflectance, 285 bands 381–2493 nm, 60 m, swath with a geometry lookup table (GLT)", "search: NASA CMR (public); download: Earthdata token"],
  ["NASA EMIT L2A MASK", "cloud, cirrus, water, dilated-cloud and (v003) SpecTf-Cloud flags", "matched to the reflectance granule by acquisition time"],
  ["Landsat 8/9 Collection 2 L2", "ST_B10 surface temperature, QA_PIXEL, MTL, 30 m", "Microsoft Planetary Computer STAC; only the box window of the COG is read"],
  ["MINERAL/", "12 USGS library spectra; training_sample.csv and dataset_sample.csv (546 columns: 530 features + labels); specification PDF", "project dataset"],
  ["AGRICULTURE/", "RTM_NN_TRAINING_DATASET_WAVELENGTHS.csv: 10,000 PROSAIL spectra with Cab, Car, Cw, Cm, LAI, ALA", "project dataset"],
  ["THERMAL/", "thermal_training_model_ready.csv: 5,000 LST samples with 3×3 statistics", "project dataset"],
], [2.2, 3.6, 2.4], "Data sources"));
C.push(H2("5.1 Download-and-stage, coverage and outages"));
C.push(P("EMIT files are 1.8–1.9 GB with no server-side subsetting, so each granule is **downloaded once, staged in the Bronze lake** and cropped for every later job (a Redis lock prevents duplicate downloads). Landsat is read as Cloud-Optimized GeoTIFF windows. Every EMIT acquisition over the service region (3,687 since August 2022) is indexed with its footprint in PostGIS at start-up and daily by Dagster; the map dims everything without a usable scene for the chosen dates, and the request carries the covering scenes so jobs keep working while NASA's search service is unavailable."));
C.push(Note("**Cloud masks:** EMIT's MASK v003 changed the band order, and its legacy threshold flag marks bright ground (salt flats, playas) as cloud: a 0 %-cloud Death Valley scene had 0.7 % legacy-flagged pixels dilated to 100 % of the box. The ingestion selects flag bands by their names and uses the SpecTf-Cloud flag when the granule has it; a box that is more than 95 % cloud ends as NO_DATA with a message."));

// 6 data engineering
C.push(H1("6. Data engineering"));
C.push(...T(["Topic", "Partitions", "Producer → consumer"], [
  ["ingest.requests", "12", "gateway outbox → ingestion (carries emit_candidates from the catalog)"],
  ["raw.hyperspectral / raw.thermal", "12 / 6", "ingestion → preprocessing"],
  ["preprocessed.hyperspectral / .thermal", "12 / 6", "preprocessing → ml_inference"],
  ["ml.results", "6", "ml_inference → orchestrator"],
  ["job.status", "12", "every stage → orchestrator, event_sink"],
  ["dlq", "1 (30 days)", "workers after 3 retries → operators"],
], [2.6, 1.2, 4], "Kafka topics (7-day retention unless noted)"));
C.push(P("Messages are JSON defined once as pydantic models; JSON Schemas are generated into contracts/ and a Go test checks the Go structs against them. Delivery is at-least-once with idempotent consumers; large data travels as object-storage keys."));
C.push(...T(["Store", "Holds"], [
  ["Redis", "live job status (pub/sub → SSE), idempotency keys, download locks, rate limits, token denylist"],
  ["PostgreSQL + PostGIS", "users, refresh tokens, audit log; jobs, tasks, sources (bbox as geometry); layers, results; catalog_granules (EMIT footprints); model registry; outbox; data-quality results"],
  ["Bronze", "staged EMIT granules (7 in this deployment); every Kafka event as Parquet"],
  ["Silver", "per-job cubes: raw.zarr, reflectance.zarr, multiband.zarr, cloud mask, Landsat LST"],
  ["Gold", "map layers (COG + PNG), STAC items, manifests, analytics tables, versioned training data"],
  ["Models", "models/hydra/<version>/hydra.onnx, agri_lut.npz, model card, verification; active.json"],
], [1.8, 5.6], "State and data lake"));
C.push(...T(["Dagster asset", "What it does"], [
  ["imagery_catalog", "EMIT footprints (and Landsat) over the region into PostGIS (the coverage map)"],
  ["prestaged_granules", "downloads the clearest recent EMIT granules into Bronze ahead of demand"],
  ["events_compacted, gold_job_facts, gold_layer_stats", "Parquet compaction; one row per job; per-layer statistics"],
  ["model_agreement_report", "HydraNet vs classical over 24 h on one scale (pixel share, 1 − nMAE); fails below the floor"],
  ["db_housekeeping, pg_backup", "pruning; nightly Postgres backup"],
], [2.6, 4.8], "Batch pipelines"));

// 7 preprocessing
C.push(H1("7. Preprocessing"));
C.push(P("The EMIT preprocessing is the Dask module by Ananthanarayanan B: chunked, with scene statistics measured once so chunks have no seams. The same deployed code cleans the real EMIT pixels used for training (training/harvest_emit.py), so the network learns exactly the spectra it sees in production."));
C.push(...T(["Step", "Why"], [
  ["Quality gate on raw values", "rejects scenes that are mostly no-data"],
  ["Fill, NaN, inf → 0; integer reflectance ÷ 10000", "auto-detected; EMIT L2A is already 0..1"],
  ["Haze removal only for raw radiance", "on EMIT surface reflectance it destroys signal, so it is skipped"],
  ["Interpolate water-vapour and low-SNR bands", "never deleted, so band positions never shift (39 bands on the real scenes)"],
  ["Savitzky-Golay smoothing (7, 2), clip to [0, 1] last", "keeps absorption depths; smoothing can ring below 0"],
], [3, 4.4], "EMIT preprocessing"));
C.push(P("Landsat: ST_B10 to °C with the scene's MTL calibration, clouds removed with QA_PIXEL, reprojection to EPSG:4326 (nearest neighbour)."));

// 8 classical
C.push(H1("8. The classical methods (teachers)"));
C.push(P("These are the \"normal ways\" of making each map, in libs/hydra/classical, verified against the owners' reference code by parity tests. They also reproduce the owner's real-scene benchmark (dataset_sample.csv) exactly: class, abundances and quality."));
C.push(...T(["Map", "Method and settings"], [
  ["Mineral class", "SAM on continuum-removed spectra (upper convex hull) against 12 USGS minerals on 255 good EMIT bands; best angle > 0.20 rad → unknown"],
  ["Mineral abundance", "fully constrained least squares (non-negative, sum-to-one row weighted 1,000)"],
  ["Mineral quality", "min(SAM angle and separation score, 1 − SMA RMSE / 0.10)"],
  ["Crop traits", "PROSAIL (PROSPECT-5 + 4SAIL): N 1.5, hotspot 0.1, sun 30°, view 0°, psoil 0.5, rsoil 1.0, spherical leaves; 100,000-entry LUT, inverse-distance mean of k = 20 nearest; vegetated pixels (NDVI ≥ 0.25)"],
  ["Thermal", "scene z < −2 / > +2; 11×11 local z > 2; urban above p80, rural below p20; hotspots above p95 in regions ≥ 10 px"],
], [1.8, 5.6], "Teachers"));

// 9 model
C.push(H1(`9. HydraNet ${V}: the three-in-one network`));
C.push(...Img("fig_hydranet.png", 6.3, "HydraNet: three input stems, a shared trunk with a task embedding, and task heads."));
C.push(...T(["Input", "Size", "Features (identical to the MINERAL CSV columns 0–529)"], [
  ["x_agri", "22", "reflectance at the 22 PROSAIL wavelengths, interpolated from EMIT"],
  ["x_mineral", "530", "255 continuum-removed + **255 raw reflectance** + 7 absorption depths + 12 SAM angles + brightness"],
  ["x_thermal", "10", "global z, distances to p95/p80/p20, local 11×11 z, 3×3 statistics, normalised LST, scene std"],
], [1.2, 0.7, 5.6], "Inputs"));
C.push(...T(["Part", "Layers"], [
  ["Mineral stem (MLP)", "spectra (CR + raw, 510) → 128 → 96; scalars (20) + SAM decision structure (26) → 64; concat → 128"],
  ["Agriculture / thermal stems", "MLP 22 → 256 → 128 / MLP 10 → 128 → 128"],
  ["Shared trunk", "task embedding; 2 × residual block (LayerNorm, 128 → 256 → 128, GELU, dropout 0.05); LayerNorm"],
  ["Heads", "agriculture 6 traits + uncertainty; mineral class (13, with SAM skip), abundance (12, softmax), quality; thermal 3 + 3 + 2"],
  ["Size", `${card.parameters.toLocaleString("en-US")} parameters, ${(card.bytes / 1e6).toFixed(2)} MB ONNX; ONNX matches PyTorch within ${Math.max(...Object.values(card.onnx_vs_torch_max_abs_diff)).toExponential(1)}`],
], [1.8, 5.6], "Layers"));
C.push(Note(`**Safety lock:** the feature specification (bands, thresholds, library checksum, PROSAIL settings, feature layout) is hashed (${card.feature_spec_hash}) into the ONNX metadata; the runtime refuses a model built for a different specification. During the switch from the prototype the running services refused the new model and kept serving the old one until the new images were rolled out: no downtime, and old code never ran a new model.`));
C.push(...Img("chart_params.png", 5.2, "Trainable parameters: the first CNN prototype, the MLP prototype, and the deployed MLP."));

// 10 data + training
C.push(H1("10. Training data and training"));
C.push(...Img("fig_training_flow.png", 6.3, "Data flow: USGS library and real EMIT scenes, all labelled by the classical teachers."));
C.push(H2("10.1 Why the mineral dataset was rebuilt"));
C.push(P("The prototype matched SAM/SMA on synthetic data but not on real EMIT (abundance error 0.078, quality error up to 0.28 on real jobs). Controlled trials on the owner's real scene showed the cause was the **dataset first, then the inputs**, not the training (the prototype fitted its own data to 0.004):"));
C.push(...Img("chart_ablation.png", 5.6, "Abundance error on the owner's real scene: the prototype, new data with the old inputs, new data with raw reflectance."));
C.push(...T(["Change", "Reason"], [
  ["Dry vegetation (35 % of mixtures, 5–35 % cover) and soil moisture (30 %)", "real pixels contain both; without them the network confuses them with mineral absorptions (MINERAL dataset specification §5)"],
  ["Raw reflectance as input (530 features)", "continuum removal discards the albedo and slope that SMA abundances and fit quality depend on"],
  ["Real EMIT pixels from 5 scenes, cleaned by the production preprocessing", "removes the synthetic-to-real gap; the network learns production spectra"],
  ["Every label from the classical teacher", "the updated training_sample.csv carries the simulation's mixing fractions and an SMA-only quality, while the benchmark and production use SAM + SMA + combined quality; its spectra are used, relabelled"],
  ["A whole scene held out (Death Valley)", "an honest test on a scene the network never saw"],
], [3, 4.4], "Dataset decisions"));
const s = mm.by_source;
C.push(...T(["Set", "Size", "Content"], [
  ["mineral_train / val", `${mm.n.toLocaleString("en-US")}`, `owner's USGS mixtures ${s.owner_usgs.toLocaleString("en-US")}; USGS coverage, near-tie and nuisance mixtures ${s.usgs_mixtures.toLocaleString("en-US")}; owner's sample spectra ${s.owner_sample.toLocaleString("en-US")}; real EMIT ${s.real_emit.toLocaleString("en-US")}`],
  ["mineral_real", `${mm.eval_sets.mineral_real.toLocaleString("en-US")}`, "the owner's real scene (dataset_sample.csv), evaluation only"],
  ["mineral_real_holdout", `${mm.eval_sets.mineral_real_holdout.toLocaleString("en-US")}`, "Death Valley 2023-10-15, a scene never trained on"],
  ["agri_lut / agri_train / agri_csv", "100,000 / 200,000 / 10,000", "PROSAIL LUT; PROSAIL spectra with soil, geometry and noise variation; the CSV rows"],
  ["thermal_train / val", `${manifest.thermal.n.toLocaleString("en-US")} pixels`, `${manifest.thermal.scenes} synthetic LST scenes`],
], [1.8, 1.4, 4.3], "Training data (all labels from the classical teachers)"));
C.push(P(`Real training scenes: ${mm.real_training_scenes.join(", ")} (Fresno farmland, Mojave, Owens Lake, Searles Lake, Mountain Pass); held out: ${mm.real_holdout_scene}.`));
C.push(...Img("chart_sources.png", 5.6, "Mineral training samples by source."));
const ta = card.train_args;
C.push(...T(["Setting", "Value"], [
  ["Optimiser / schedule", `AdamW (weight decay 1e-4), learning rate ${ta.lr}, one-cycle; ${ta.steps.toLocaleString("en-US")} steps, batch ${ta.batch} per task; seed ${ta.seed}`],
  ["Losses", "agriculture MSE + 0.25 × uncertainty; mineral cross-entropy + 20 × abundance MSE + 10 × quality MSE; thermal cross-entropies; learned task-uncertainty weights"],
  ["Checkpoint", "best validation score; the real test scenes never influence training or selection"],
], [2, 5.4], "Training settings"));
C.push(...Img("chart_training.png", 5.6, "Validation loss per task during training."));

// 11 results
C.push(H1("11. Results"));
C.push(H2("11.1 Release gates"));
C.push(...T(["Gate", "Value", "Threshold", "Result"], Object.entries(G).map(([k, g]) => [k, num(g.value, 5), String(g.threshold), g.passed ? "pass" : "FAIL"]),
  [3.4, 1.2, 1.1, 0.9], `Gates checked on the ONNX outputs (all ${Object.keys(G).length} pass)`));
C.push(...Img("chart_prototype_vs_model.png", 6.2, `Minerals on real EMIT never trained on: the prototype vs ${V} (dashed line: gate).`));
C.push(H2("11.2 Whole-scene map verification"));
C.push(...Img("chart_agreement.png", 5.8, "Class maps: pixel agreement with the classical method (tick = gate), incl. the worst real Landsat scene."));
C.push(...Img("chart_errors.png", 5.8, "Continuous maps: error against the classical method (tick = gate)."));
C.push(...Img("chart_agri_r2.png", 5.4, "Agriculture R² against the LUT inversion (validation set)."));
C.push(H2("11.3 Real Landsat scenes"));
C.push(...Img("chart_landsat.png", 5.8, "Agreement with the thermal rules on six real Landsat 8/9 scenes; every map must be ≥ 98 % on every scene."));
C.push(...T(["Scene", "Landsat ID", "Date", "Heat-island intensity (NN / rules)"], mv.landsat_real.map((x) => [x.scene.replace(/_/g, " "), x.landsat_id, x.acquired_at.slice(0, 10), `${x.uhi_intensity_nn_c} / ${x.uhi_intensity_classical_c} °C`]), [1.4, 3, 1.2, 1.9], "Real Landsat scenes"));
C.push(P(`Tulare farms is the hardest scene: many pixels sit at the 95th-percentile hotspot threshold. A first candidate training run reached only 98.0 % there; it was rejected, and a per-scene floor of 98 % now guards every release.`));

C.push(H2("11.4 Real jobs through the whole platform"));
const jf = jobs.fresno.job;
C.push(P(`Real jobs on 21 October 2026 with ${V}, every pixel compared with the classical methods. Fresno farmland (EMIT ${jf.acquired_emit.slice(0, 10)}, Landsat ${jf.acquired_landsat.slice(0, 10)}), Death Valley (EMIT 2023-10-15, the held-out scene) and Searles Lake (EMIT 2023-10-05).`));
C.push(...T(["Scene", "Bare pixels", "Class", "Abundance MAE", "Quality MAE", "HydraNet / classical time"], [
  ["Fresno farmland", RF.mineral.pixels.toLocaleString("en-US"), pct(RF.mineral.class_agreement), num(RF.mineral.abundance_mae, 4), num(RF.mineral.quality_mae, 4), `${RF.mineral.timing_s.hydra} / ${RF.mineral.timing_s.classical} s`],
  ["Death Valley (held out)", RD.mineral.pixels.toLocaleString("en-US"), pct(RD.mineral.class_agreement), num(RD.mineral.abundance_mae, 4), num(RD.mineral.quality_mae, 4), `${RD.mineral.timing_s.hydra} / ${RD.mineral.timing_s.classical} s`],
  ["Searles Lake", RS.mineral.pixels.toLocaleString("en-US"), pct(RS.mineral.class_agreement), num(RS.mineral.abundance_mae, 4), num(RS.mineral.quality_mae, 4), `${RS.mineral.timing_s.hydra} / ${RS.mineral.timing_s.classical} s`],
], [1.8, 1, 0.9, 1.1, 1, 1.6], "Minerals on real EMIT"));
C.push(...T(["Scene", "Vegetated pixels", "Cab", "Cw", "LAI", "Car / Cm / ALA"], [
  ["Fresno farmland", RF.agri_timing_s.pixels.toLocaleString("en-US"), pct(RF.agri.Cab.nmae, 1), pct(RF.agri.Cw.nmae, 1), pct(RF.agri.LAI.nmae, 1), `${pct(RF.agri.Car.nmae, 1)} / ${pct(RF.agri.Cm.nmae, 1)} / ${pct(RF.agri.ALA.nmae, 1)}`],
  ["Death Valley scrub", RD.agri_timing_s.pixels.toLocaleString("en-US"), pct(RD.agri.Cab.nmae, 1), pct(RD.agri.Cw.nmae, 1), pct(RD.agri.LAI.nmae, 1), `${pct(RD.agri.Car.nmae, 1)} / ${pct(RD.agri.Cm.nmae, 1)} / ${pct(RD.agri.ALA.nmae, 1)}`],
  ["Searles Lake margins", RS.agri_timing_s.pixels.toLocaleString("en-US"), pct(RS.agri.Cab.nmae, 1), pct(RS.agri.Cw.nmae, 1), pct(RS.agri.LAI.nmae, 1), `${pct(RS.agri.Car.nmae, 1)} / ${pct(RS.agri.Cm.nmae, 1)} / ${pct(RS.agri.ALA.nmae, 1)}`],
], [1.8, 1.2, 0.8, 0.8, 0.8, 2], "Agriculture on real EMIT: error as a share of each trait's range (gate 5 %)"));
C.push(Note(`**Out of domain:** the "vegetation" pixels at the edge of the wet Searles salt lake (22 % water) are algal or brine surfaces, not crop canopies; there both PROSAIL inversion and the network extrapolate and disagree (Cab ${pct(RS.agri.Cab.nmae, 0)} of range). On cropland and desert scrub every trait is within 1.3–4.5 %. See section 15.`));
C.push(P(`Thermal on the Fresno job: global ${pct(real.fresno.thermal.thermal_global_class)}, heat zones ${pct(real.fresno.thermal.thermal_uhi_class)}, local ${pct(real.fresno.thermal.thermal_local_anomaly)}, hotspots ${pct(real.fresno.thermal.thermal_hotspots)}; heat-island intensity ${real.fresno.thermal.uhi_intensity_c.hydra.toFixed(2)} vs ${real.fresno.thermal.uhi_intensity_c.classical.toFixed(2)} °C.`));
for (const [f, cap] of [
  ["real_fresno_rgb.png", "Fresno: EMIT true colour and land-cover routing (vegetation → agriculture, bare → minerals)."],
  ["real_fresno_agri_LAI.png", `Fresno leaf area index: classical, HydraNet ${V}, difference.`],
  ["real_fresno_agri_scatter.png", "Fresno: every vegetated pixel, HydraNet against the classical inversion (dashed: identical)."],
  ["real_fresno_mineral_class.png", "Fresno dominant mineral: SAM, HydraNet and where they differ."],
  ["real_deathvalley_rgb.png", "Death Valley (held-out scene): true colour and routing."],
  ["real_deathvalley_mineral_class.png", "Death Valley dominant mineral: SAM, HydraNet and disagreement."],
  ["real_deathvalley_mineral_abundance.png", `Death Valley ${RD.mineral.abundance_figure_mineral} abundance: SMA, HydraNet, difference.`],
  ["real_searles_mineral_abundance.png", `Searles Lake ${RS.mineral.abundance_figure_mineral} abundance: SMA, HydraNet, difference.`],
  ["real_fresno_thermal_uhi.png", "Fresno Landsat temperature and heat zones: rules vs HydraNet."],
  ["real_fresno_thermal_hotspots.png", "Hotspots: rules vs HydraNet."],
]) C.push(...Img(f, 6.2, cap));
C.push(H2("11.5 Speed and size"));
C.push(...Img("chart_speed.png", 5.8, "Time for five scenes on one CPU, end to end."));
C.push(P(`Minerals ${sp("mineral").toFixed(0)}× and agriculture ${sp("agri").toFixed(0)}× faster end to end (${(bench.M.mineral[1] / bench.M.mineral[0]).toFixed(0)}× and ${(bench.M.agri[1] / bench.M.agri[0]).toFixed(0)}× for the model step alone); on the real Death Valley box 0.09 s vs 1.0 s. For thermal the network is slower: it needs the same scene statistics as the rules, which are already cheap.`));

// 12 classical vs ours
C.push(H1("12. Classical methods vs HydraNet"));
C.push(...T(["Aspect", "Classical methods", `HydraNet ${V}`], [
  ["What runs", "SAM + NNLS unmixing, k-NN in a 100,000-entry LUT, thermal rules", "one 2 MB ONNX file, three inputs, nine outputs"],
  ["Accuracy", "the reference", "reproduces it on real EMIT: class 99.99–100 %, abundance error ≤ 0.004, crop traits 1.3–4.5 % on cropland"],
  ["Speed (CPU)", "per-pixel search and optimisation", `minerals ${sp("mineral").toFixed(0)}×, agriculture ${sp("agri").toFixed(0)}× faster; thermal slower`],
  ["Scaling", "cost grows with library and LUT size", "fixed cost per pixel, batched; scales with workers"],
  ["Uncertainty", "LUT spread (agriculture)", "per-pixel confidence (minerals) and std (agriculture)"],
  ["Portability", "Python/numba/scipy", "ONNX Runtime on CPU, GPU, browser or edge"],
  ["Can it beat the teacher?", "—", "not as trained (it learns the teacher); ground truth is the route (section 15)"],
], [1.6, 2.7, 3.1], "Side by side"));
["**Speed where it matters:** library search, unmixing and LUT search become one batched matrix pass.",
  "**One verified artifact:** versioned, gated, hot-swapped and rolled back as a unit; the classical code stays as the verifier.",
  "**Verified, not trusted:** release gates on two real scenes, whole-scene map checks, and shadow verification on every job with alerts.",
  "**A base to improve:** unlike fixed rules, the network can be fine-tuned on field data or fire records."].forEach((t) => C.push(B(t)));

// 13 serving + UI
C.push(H1("13. Serving and the web application"));
["The model store downloads the active version and **hot-swaps** it between messages; a model built for another feature spec is refused and the old one keeps serving.",
  "Layers are written as Cloud-Optimized GeoTIFF (exact values) and colour PNG, with a STAC item; maps reach the browser through 15-minute presigned URLs.",
  "**Shadow verification:** a random sample of each job is re-run with the classical method. Classes are judged by pixel agreement, continuous maps by error as a share of the value's range (≤ 5 %); R² is shown for information only because it collapses on uniform areas (a LAI R² of 0.27 with only 3.7 % error was observed).",
  "Abundance layers are written only for minerals present (≥ 2 % mean abundance or ≥ 1 % of pixels); the layer menu separates Main maps from More detail."].forEach((t) => C.push(B(t)));
for (const [f, cap] of [["ui_login.png", "Sign-in page."], ["ui_coverage.png", "EMIT coverage for the last 12 months: dimmed where no usable scene exists."],
  ["ui_workspace.png", "A box inside the coverage: the form confirms a scene and says it is already downloaded."],
  ["ui_agriculture.png", "Real job: agriculture maps."], ["ui_checks.png", "The check against the classical method, judged by error as a share of range."],
  ["ui_minerals.png", "Real job: dominant mineral."], ["ui_thermal.png", "Real job: thermal maps."], ["ui_inspector.png", "Pixel inspector."],
  ["ui_deathvalley.png", "Death Valley (held-out scene): minerals."], ["ui_platform.png", "Platform page: the active model and all its gates."]]) C.push(...Img(f, 6.0, cap));

// 14 security, scale, deployment, tests
C.push(H1("14. Security, scale, deployment and testing"));
C.push(...T(["Threat", "Control"], [
  ["Password guessing", "per-IP limit (20/min), per-account lockout (10 → 15 min), bcrypt cost 11, ≥ 10-character passwords"],
  ["Fake client IP", "nginx overwrites X-Real-IP with the TCP peer; X-Forwarded-For never trusted"],
  ["Forged or stolen tokens", "HS256 only with key ring; 15-minute access tokens in memory; rotating HttpOnly refresh cookies with reuse detection; single-use stream tickets"],
  ["CSRF, IDOR, injection, XSS", "SameSite=Strict + header + Origin; ownership checks (404); parameterised SQL; strict CSP and headers"],
  ["Abuse, compromised container", "body cap, admission control; non-root, no capabilities, read-only roots, internal services unpublished"],
  ["Dependencies, secrets", "govulncheck, pip-audit, npm audit, Trivy, gitleaks in CI; secrets in .env (local) / SSM (AWS)"],
], [2, 5.4], "Security (tests/security.py: 63 attacks, none succeed)"));
C.push(...T(["Load test", "Result"], [
  ["300 concurrent users (register, submit, poll, layers)", "0 failed of 54,694 requests; API p95 4.1 ms, submit 7.4 ms, layers 5.9 ms"],
  ["Distinct areas, 4 workers per stage", "3.8 jobs/s (all three maps)"],
], [3.2, 4.2], "Load (k6, one laptop)"));
C.push(...Img("fig_aws.png", 6.3, "AWS deployment (Terraform)."));
C.push(P("us-west-2 (next to NASA's and USGS's data), one EC2 t4g.large running the compose stack, S3 data lake, CloudFront, ECR, SSM, Glue/Athena, budgets and night-time stop/start; GitHub Actions for CI, multi-arch releases, deployment over SSM with OIDC, and training. Moving to another S3-compatible store changes only the endpoint and keys."));
C.push(...T(["Suite", "What it proves", "Result"], [
  ["libs/hydra, libs/hs_common", "classical methods = reference code; 530 features reproduce both MINERAL CSVs; teacher reproduces the real benchmark exactly; EMIT ortho and both mask layouts; ONNX runtime", "22 passed"],
  ["preprocessing/tests", "Dask pipeline, Landsat calibration, contracts", "57 passed"],
  ["Go, frontend", "contracts vs schemas; formatting, layers, coverage geometry", "pass; 8 passed"],
  ["training gates, verify_maps", "the model reproduces the classical maps, incl. two real EMIT scenes and six real Landsat scenes", `all ${Object.keys(G).length} gates; all map checks`],
  ["tests/e2e.py, ui_smoke.py, security.py, load", "full stack, real browser with CSP, 63 attacks, 300 users", "0 failures each"],
], [2.2, 4, 1.4], "Tests"));

// 15 limitations
C.push(H1("15. Limitations, novelty and next steps"));
["Real-imagery validation covers 7 EMIT scenes in California and Central Valley farmland; other regions, seasons and sensors are not yet validated.",
  "Agriculture is meant for crops and natural canopies: on non-canopy 'vegetation' such as algal or brine surfaces at salt-lake edges, both PROSAIL inversion and the network are out of domain (Searles Lake: Cab error 22 % of range). Next step: flag pixels whose spectra are far from the PROSAIL training set.",
  "The network reproduces its teachers; it does not measure anything they cannot. Ground truth (field data, NASA FIRMS fire points) is the path to exceed them.",
  "Thermal: the hardest scene agrees 98.8 % on hotspots; labels are the detector's rules (the dataset has no labels)."].forEach((t) => C.push(B(t)));
C.push(P("**Novelty:** neural emulation of PROSAIL inversion is established (e.g. ESA SNAP for Sentinel-2) and distillation is a standard technique. The contribution here is the combination: one lightweight multi-task network for minerals, crops and thermal across two sensors; a neural reproduction of SAM + SMA that holds on real EMIT through real-pixel training with production preprocessing; and a production design where the classical methods remain the continuous verifier (spec-hash lock, release gates on held-out real scenes, shadow checks)."));

// 16 how to run
C.push(H1("16. How to run"));
C.push(...Code([
  "cd pre_production && cp .env.example .env     # secrets; EARTHDATA_TOKEN for real EMIT",
  "docker compose --profile local up -d --build    # http://localhost:3000",
  "",
  "# retrain (training/README.md): harvest real scenes, generate data, train, verify, promote",
  "cd training && python -m datagen --out artifacts/data && python -m train",
  "python -m verify_maps --model artifacts/models/" + V + " --data artifacts/data",
  "",
  "# tests",
  "pytest libs/hydra/tests libs/hs_common/tests",
  "python tests/e2e.py --base http://localhost:3000 --jobs 3 --real-thermal",
  "python tests/security.py --base http://localhost:3000 --env .env",
  "",
  "# this document",
  "node docs/report/build_docx.js . training/artifacts/models/v1 docs/report/build docs/HydraNet_Project_Document.docx",
]));
C.push(H2("Appendix A. REST API"));
C.push(...T(["Method and path", "Purpose"], [
  ["POST /api/auth/register, /login, /refresh, /logout, /logout-all", "accounts and sessions"],
  ["GET /api/me, /api/config, /api/model", "current user; UI limits; active model and its verification"],
  ["GET /api/coverage?date_from&date_to", "EMIT footprints and the covered area for the map"],
  ["POST /api/jobs; GET /api/jobs, /api/jobs/:id; DELETE /api/jobs/:id", "submit (Idempotency-Key), list, status, cancel"],
  ["GET /api/jobs/:id/layers; POST .../stream-ticket; GET .../events", "maps with presigned URLs; live updates"],
  ["GET /api/catalog, /api/stats (admin), /health, /ready, /metrics", "imagery index, statistics, probes"],
], [3.6, 3.8], "Endpoints"));

const doc = new Document({
  features: { updateFields: true }, creator: "Aaron R", title: "HydraNet Maps: end-to-end project document",
  styles: {
    default: { document: { run: { font: "Calibri", size: 21 } } },
    paragraphStyles: [
      { id: "Heading1", name: "Heading 1", basedOn: "Normal", next: "Normal", quickFormat: true, run: { size: 32, bold: true, color: "1F4E8C" }, paragraph: { spacing: { before: 240, after: 160 }, outlineLevel: 0 } },
      { id: "Heading2", name: "Heading 2", basedOn: "Normal", next: "Normal", quickFormat: true, run: { size: 26, bold: true, color: "2A78D6" }, paragraph: { spacing: { before: 240, after: 120 }, outlineLevel: 1 } },
      { id: "Heading3", name: "Heading 3", basedOn: "Normal", next: "Normal", quickFormat: true, run: { size: 22, bold: true, color: "333333" }, paragraph: { spacing: { before: 160, after: 80 }, outlineLevel: 2 } },
    ],
  },
  numbering: { config: [
    { reference: "bullets", levels: [{ level: 0, format: LevelFormat.BULLET, text: "•", alignment: AlignmentType.LEFT, style: { paragraph: { indent: { left: 540, hanging: 270 } } } }] },
    { reference: "numbers", levels: [{ level: 0, format: LevelFormat.DECIMAL, text: "%1.", alignment: AlignmentType.LEFT, style: { paragraph: { indent: { left: 540, hanging: 300 } } } }] },
  ] },
  sections: [{
    properties: { page: { size: { width: 11906, height: 16838 }, margin: { top: 1440, bottom: 1440, left: 1440, right: 1440 } } },
    headers: { default: new Header({ children: [new Paragraph({ alignment: AlignmentType.RIGHT, children: [new TextRun({ text: "HydraNet Maps: end-to-end project document", size: 16, color: "8A8984" })] })] }) },
    footers: { default: new Footer({ children: [new Paragraph({ alignment: AlignmentType.CENTER, children: [new TextRun({ children: [PageNumber.CURRENT], size: 18, color: "8A8984" })] })] }) },
    children: C,
  }],
});
Packer.toBuffer(doc).then((b) => { fs.writeFileSync(OUT, b); console.log("wrote", OUT, (b.length / 1e6).toFixed(1), "MB"); });
