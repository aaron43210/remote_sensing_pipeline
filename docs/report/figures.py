"""
Diagrams and charts for the project document (PNG, 200 dpi).

    python docs/report/figures.py <repo> <model_dir> <bench.json> <out_dir>
"""
import json
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch

REPO, MODEL, BENCH, OUT = Path(sys.argv[1]), Path(sys.argv[2]), json.load(open(sys.argv[3])), Path(sys.argv[4])
OUT.mkdir(parents=True, exist_ok=True)
card = json.load(open(MODEL / "model_card.json"))
mv = json.load(open(MODEL / "map_verification.json"))
metrics = json.load(open(MODEL / "metrics.json"))
manifest = json.load(open(REPO / "training/artifacts/data/manifest.json"))

INK, INK2, GRID = "#0b0b0b", "#52514e", "#e4e3df"
BLUE, ORANGE, AQUA = "#2a78d6", "#eb6834", "#1baf7a"
TINT = {BLUE: "#e6f0fb", ORANGE: "#fdeee7", AQUA: "#e3f6ef", INK2: "#f1f0ee"}
plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10, "text.color": INK, "axes.edgecolor": GRID,
                     "axes.labelcolor": INK2, "xtick.color": INK2, "ytick.color": INK2, "axes.spines.top": False,
                     "axes.spines.right": False, "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.6,
                     "axes.axisbelow": True})


def save(fig, name):
    fig.savefig(OUT / name, dpi=200, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def box(ax, x, y, w, h, title, sub=None, color=INK2, fs=9):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.02,rounding_size=0.08",
                                fc=TINT.get(color, "#f6f6f4"), ec=color, lw=1.4))
    if sub:
        ax.text(x + w / 2, y + h * 0.66, title, ha="center", va="center", fontsize=fs, weight="bold")
        ax.text(x + w / 2, y + h * 0.3, sub, ha="center", va="center", fontsize=fs - 2, color=INK2)
    else:
        ax.text(x + w / 2, y + h / 2, title, ha="center", va="center", fontsize=fs, weight="bold")


def arrow(ax, a, b, label=None, style="-|>", ls="-", rad=0.0):
    ax.add_patch(FancyArrowPatch(a, b, arrowstyle=style, mutation_scale=11, color=INK2, lw=1.2,
                                 linestyle=ls, connectionstyle=f"arc3,rad={rad}"))
    if label:
        ax.text((a[0] + b[0]) / 2, (a[1] + b[1]) / 2 + 0.12, label, ha="center", va="bottom", fontsize=7,
                color=INK2, bbox=dict(fc="white", ec="none", pad=0.5))


def canvas(w, h):
    fig, ax = plt.subplots(figsize=(w, h))
    ax.set_xlim(0, w), ax.set_ylim(0, h), ax.axis("off")
    return fig, ax


# ── diagrams ────────────────────────────────────────────────────────────────
fig, ax = canvas(13, 7.4)
box(ax, 0.2, 5.9, 1.8, 0.9, "Browser", "React SPA", BLUE)
box(ax, 2.6, 5.9, 2.1, 0.9, "nginx / CloudFront", "TLS, CSP, rate limit")
box(ax, 5.3, 5.9, 2.3, 0.9, "api_gateway (Go)", "auth, jobs, coverage", BLUE)
box(ax, 8.3, 6.3, 2.0, 0.75, "PostgreSQL + PostGIS", "jobs, catalog, outbox", INK2, fs=8)
box(ax, 8.3, 5.35, 2.0, 0.75, "Redis", "status, locks, limits", INK2, fs=8)
box(ax, 10.9, 5.9, 1.9, 0.9, "orchestrator (Go)", "state machine", BLUE)
box(ax, 4.7, 3.9, 3.5, 0.8, "Kafka (KRaft)", "8 topics, JSON contracts, DLQ", ORANGE)
box(ax, 0.2, 1.9, 2.3, 0.95, "ingestion", "EMIT (CMR / catalog)\nLandsat (STAC windows)", AQUA, fs=8)
box(ax, 3.0, 1.9, 2.3, 0.95, "preprocessing", "Dask cleaning (EMIT)\nLandsat calibration", AQUA, fs=8)
box(ax, 5.8, 1.9, 2.3, 0.95, "ml_inference", "HydraNet ONNX + shadow\ncheck vs classical", AQUA, fs=8)
box(ax, 8.6, 1.9, 2.0, 0.95, "event_sink", "events -> Parquet", AQUA, fs=8)
box(ax, 11.0, 1.9, 1.8, 0.95, "Dagster", "catalog, batch", INK2, fs=8)
box(ax, 0.2, 0.2, 12.6, 0.95, "Object storage: MinIO (local) / Amazon S3 (AWS)",
    "bronze: staged EMIT granules + events  ·  silver: job cubes (Zarr)  ·  gold: COG + PNG maps, STAC, Parquet  ·  models")
arrow(ax, (2.0, 6.35), (2.6, 6.35), "HTTPS")
arrow(ax, (4.7, 6.35), (5.3, 6.35))
arrow(ax, (7.6, 6.55), (8.3, 6.65)), arrow(ax, (7.6, 6.15), (8.3, 5.75))
arrow(ax, (6.45, 5.9), (6.45, 4.7), "outbox -> ingest.requests")
arrow(ax, (8.2, 4.4), (11.6, 5.9), rad=0.15)
ax.text(10.9, 4.35, "job.status / ml.results", fontsize=7, color=INK2)
arrow(ax, (11.85, 5.9), (8.2, 4.15), rad=-0.15, ls="--")
for x, tx in ((1.35, 4.9), (4.15, 5.7), (6.95, 7.0), (9.6, 8.0)):
    arrow(ax, (x, 2.85), (tx, 3.9), style="<|-|>")
for x in (1.35, 4.15, 6.95, 9.6, 11.9):
    arrow(ax, (x, 1.9), (x, 1.15), style="<|-|>")
ax.plot([1.0, 1.0], [6.8, 7.25], color=INK2, lw=1.2)
ax.text(1.0, 7.3, "SSE live status  <-  Redis pub/sub", fontsize=7, color=INK2)
save(fig, "fig_architecture.png")

fig, ax = canvas(13, 3.6)
steps = [("POST /api/jobs", "gateway", BLUE), ("ingest.requests", "topic", ORANGE), ("ingestion", "EMIT / Landsat", AQUA),
         ("raw.*", "topics", ORANGE), ("preprocessing", "clean, calibrate", AQUA), ("preprocessed.*", "topics", ORANGE),
         ("ml_inference", "HydraNet + check", AQUA), ("ml.results", "topic", ORANGE), ("orchestrator", "DONE / PARTIAL", BLUE)]
x = 0.1
for i, (t, s, c) in enumerate(steps):
    box(ax, x, 1.6, 1.3, 1.1, t, s, c, fs=7.5)
    if i:
        arrow(ax, (x - 0.15, 2.15), (x, 2.15))
    x += 1.45
ax.text(0.1, 0.9, "Every stage publishes job.status (Redis pub/sub -> SSE). Failures retry 3x with back-off, then go to the dlq topic.",
        fontsize=8, color=INK2)
ax.text(0.1, 0.5, "The request carries catalogued EMIT scenes covering the box, so ingestion keeps working while NASA's search (CMR) is down.",
        fontsize=8, color=INK2)
save(fig, "fig_pipeline.png")

fig, ax = canvas(13, 6.2)
rows = [("x_agri (22 bands)", "AgriStem: MLP 22 -> 256 -> 128", BLUE, 4.7),
        ("x_mineral (530 features)", "MineralStem (MLP): CR + raw 510 -> 128 -> 96\n+ 20 scalars & SAM structure -> 64 -> 128", ORANGE, 2.85),
        ("x_thermal (10 features)", "ThermalStem: MLP 10 -> 128 -> 128", AQUA, 1.0)]
for t, s, c, y in rows:
    box(ax, 0.1, y, 2.2, 0.9, t, None, c, fs=8.5)
    box(ax, 2.7, y - 0.1, 3.0, 1.1, "stem", s, c, fs=8)
    arrow(ax, (2.3, y + 0.45), (2.7, y + 0.45))
    arrow(ax, (5.7, y + 0.45), (6.3, 3.3))
box(ax, 6.3, 2.5, 2.3, 1.6, "Shared trunk", "+ task embedding\n2 x ResBlock(128)\nLayerNorm, GELU, dropout 0.05", INK2, fs=8.5)
heads = [("Agriculture head", "Cab Car Cw Cm LAI ALA\n+ uncertainty (std)", BLUE, 4.7),
         ("Mineral heads", "class (13) · abundance (12) · quality\nSAM skip into the class head", ORANGE, 2.85),
         ("Thermal head", "global 3 · UHI 3 · hotspot · local", AQUA, 1.0)]
for t, s, c, y in heads:
    box(ax, 9.4, y - 0.1, 3.4, 1.1, t, s, c, fs=8)
    arrow(ax, (8.6, 3.3), (9.4, y + 0.45))
ax.text(6.5, 0.3, f"{card['parameters']:,} parameters · ONNX opset 17 · {card['bytes'] / 1e6:.2f} MB · 3 inputs, 9 outputs · "
        "normalisation and scaling inside the graph · feature_spec_hash in metadata", fontsize=8, color=INK2, ha="center")
save(fig, "fig_hydranet.png")

fig, ax = canvas(13, 4.2)
box(ax, 0.1, 2.5, 2.4, 1.3, "USGS library", "12 minerals\n+ NPV & moisture\nnuisance models", INK2, fs=8)
box(ax, 0.1, 0.6, 2.4, 1.3, "Real EMIT scenes", "5 training scenes\n1 held-out scene\n(Earthdata)", AQUA, fs=8)
box(ax, 3.0, 0.6, 2.4, 1.3, "Production\npreprocessing", "the deployed Dask\npipeline (harvest_emit)", AQUA, fs=8)
box(ax, 3.0, 2.5, 2.4, 1.3, "datagen.py", "mixtures, near-ties,\nPROSAIL, LST scenes", AQUA, fs=8)
box(ax, 5.9, 1.55, 2.4, 1.3, "Classical teachers", "SAM + SMA + quality\nLUT inversion k = 20\nthermal rules", ORANGE, fs=8)
box(ax, 8.8, 1.55, 1.9, 1.3, "train.py", "multi-task loss\nAdamW, one-cycle", BLUE, fs=8)
box(ax, 11.1, 1.55, 1.8, 1.3, "Gates", "ONNX export\nverify_maps\npromote", INK2, fs=8)
arrow(ax, (2.5, 3.15), (3.0, 3.15)), arrow(ax, (2.5, 1.25), (3.0, 1.25))
arrow(ax, (5.4, 3.15), (5.9, 2.4)), arrow(ax, (5.4, 1.25), (5.9, 2.0))
arrow(ax, (8.3, 2.2), (8.8, 2.2)), arrow(ax, (10.7, 2.2), (11.1, 2.2))
ax.text(6.5, 0.1, "Every label is the classical methods' own output, so the network learns to draw the same map; the gates compare against them.",
        ha="center", fontsize=8, color=INK2)
save(fig, "fig_training_flow.png")

fig, ax = canvas(13, 4.8)
box(ax, 0.1, 3.3, 1.8, 0.9, "Users", None, BLUE)
box(ax, 2.4, 3.3, 2.2, 0.9, "CloudFront", "HTTPS, edge cache")
box(ax, 5.1, 2.6, 3.4, 1.9, "EC2 t4g.large (arm64)", "docker compose: nginx, gateway,\norchestrator, workers, Kafka,\nPostgres, Redis", BLUE, fs=8.5)
box(ax, 9.1, 3.6, 3.7, 0.8, "S3 data lake", "bronze / silver / gold, SSE, private", ORANGE, fs=8)
box(ax, 9.1, 2.6, 3.7, 0.8, "ECR + SSM Parameter Store", "images, secrets", INK2, fs=8)
box(ax, 0.1, 0.4, 3.0, 1.2, "GitHub Actions", "ci, release (multi-arch),\ndeploy via SSM (OIDC), train", INK2, fs=8)
box(ax, 3.6, 0.4, 2.8, 1.2, "Athena + Glue", "SQL over the S3 gold Parquet", AQUA, fs=8)
box(ax, 6.9, 0.4, 2.8, 1.2, "Budgets + Scheduler", "alerts 25-100 %,\nnight stop/start", AQUA, fs=8)
box(ax, 10.2, 0.4, 2.6, 1.2, "NASA / USGS (us-west-2)", "EMIT, Landsat", AQUA, fs=8)
arrow(ax, (1.9, 3.75), (2.4, 3.75)), arrow(ax, (4.6, 3.75), (5.1, 3.75))
arrow(ax, (8.5, 4.0), (9.1, 4.0), style="<|-|>"), arrow(ax, (8.5, 3.0), (9.1, 3.0), style="<|-|>")
arrow(ax, (3.1, 1.6), (5.1, 2.7)), arrow(ax, (11.5, 1.6), (8.5, 2.75), rad=0.1)
save(fig, "fig_aws.png")


# ── charts ──────────────────────────────────────────────────────────────────
def hbar_gates(rows, name, xlabel, fmt, xlim=None, color=BLUE, scale=1.0):
    fig, ax = plt.subplots(figsize=(8, 0.42 * len(rows) + 0.9))
    names = [r[0] for r in rows]
    vals = [r[1] * scale for r in rows]
    ax.barh(names, vals, color=color, height=0.55)
    for i, (v, r) in enumerate(zip(vals, rows)):
        ax.text(v, i, " " + fmt(v), va="center", fontsize=8)
        ax.plot([r[2] * scale] * 2, [i - 0.35, i + 0.35], color=INK2, lw=1.5)
    if xlim:
        ax.set_xlim(*xlim)
        ax.set_xticks([t for t in ax.get_xticks() if t <= 100])
    ax.set_xlabel(xlabel), ax.invert_yaxis(), ax.grid(axis="y", visible=False)
    save(fig, name)


g = mv["gates"]
hbar_gates([(k.replace("_", " "), v["value"], v["threshold"]) for k, v in g.items() if v["metric"] == "pixel_agreement"],
           "chart_agreement.png", "Pixel agreement with the classical map (%)  |  tick = gate", lambda v: f"{v:.2f} %",
           (96.5, 100.6), scale=100)
hbar_gates([(f"{k.replace('_', ' ')} ({v['metric']})", v["value"], v["threshold"]) for k, v in g.items()
            if v["metric"] in ("mae", "nmae")], "chart_errors.png", "Error vs classical (lower is better)  |  tick = gate",
           lambda v: f"{v:.4f}", color=ORANGE)

ls = mv["landsat_real"]
fig, ax = plt.subplots(figsize=(8, 3.2))
layers = [("thermal_global_class", "global class", BLUE), ("thermal_uhi_class", "heat zones", ORANGE),
          ("thermal_local_anomaly", "local anomaly", AQUA), ("thermal_hotspots", "hotspots", INK2)]
for j, (l, lab, c) in enumerate(layers):
    ax.bar([i + (j - 1.5) * 0.2 for i in range(len(ls))], [s["agreement"][l] * 100 for s in ls], 0.18, color=c, label=lab)
ax.axhline(98, color=INK2, lw=1, ls="--")
ax.text(-0.45, 98.05, "per-scene floor 98 %", fontsize=7, color=INK2, ha="left", va="bottom")
ax.set_xticks(range(len(ls)), [s["scene"].replace("_", " ") for s in ls], fontsize=8)
ax.set_ylim(97.5, 100.05), ax.set_ylabel("Agreement (%)"), ax.grid(axis="x", visible=False)
ax.legend(ncol=4, fontsize=8, frameon=False, loc="lower center", bbox_to_anchor=(0.5, 1.0))
save(fig, "chart_landsat.png")

T = BENCH["T"]
fig, ax = plt.subplots(figsize=(8, 2.8))
for k, (off, col, lab) in enumerate(((-0.18, BLUE, f"HydraNet {card['version']} (ONNX, CPU)"), (0.18, ORANGE, "Classical method"))):
    vals = [T[t][k] for t in ("mineral", "agri", "thermal")]
    ax.barh([i + off for i in range(3)], vals, 0.34, color=col, label=lab)
    for i, v in enumerate(vals):
        ax.text(v, i + off, f" {v:.2f} s", va="center", fontsize=8)
ax.set_yticks(range(3), ["Minerals", "Agriculture", "Thermal"]), ax.invert_yaxis()
ax.set_xlabel("Total time for 5 scenes, end to end (s)"), ax.legend(frameon=False, fontsize=8, loc="upper right")
ax.grid(axis="y", visible=False)
save(fig, "chart_speed.png")

fig, ax = plt.subplots(figsize=(8, 2.4))
p = [("CNN (first prototype)", 1.192529, INK2), ("MLP prototype (275 inputs)", 0.469390, INK2), (f"MLP {card['version']} (530 inputs, deployed)", card["parameters"] / 1e6, BLUE)]
ax.barh([r[0] for r in p], [r[1] for r in p], color=[r[2] for r in p], height=0.5)
for i, r in enumerate(p):
    ax.text(r[1], i, f" {r[1]:.2f} M", va="center", fontsize=8)
ax.set_xlabel("Trainable parameters (millions)"), ax.invert_yaxis(), ax.grid(axis="y", visible=False)
save(fig, "chart_params.png")

h = metrics["history"]
fig, ax = plt.subplots(figsize=(8, 3))
for k, c, lab in (("agri", BLUE, "agriculture"), ("mineral", ORANGE, "mineral"), ("thermal", AQUA, "thermal")):
    ax.plot([r["step"] for r in h], [r["loss"][k] for r in h], color=c, lw=2, label=lab)
ax.set_yscale("log"), ax.set_xlabel("Training step"), ax.set_ylabel("Task loss (log)")
ax.legend(frameon=False, fontsize=8, ncol=3, loc="lower center", bbox_to_anchor=(0.5, 1.0))
save(fig, "chart_training.png")

mm = manifest["mineral"]
src = mm["by_source"]
fig, ax = plt.subplots(figsize=(8, 2.4))
labels = {"owner_usgs": "Owner's USGS mixtures", "usgs_mixtures": "USGS mixtures (coverage, near-ties, NPV/moisture)",
          "owner_sample": "training_sample.csv spectra", "real_emit": "Real EMIT pixels (5 scenes)"}
ks = list(src)
ax.barh([labels[k] for k in ks], [src[k] / 1000 for k in ks], color=[INK2, INK2, INK2, AQUA], height=0.5)
for i, k in enumerate(ks):
    ax.text(src[k] / 1000, i, f" {src[k]:,}", va="center", fontsize=8)
ax.set_xlabel("Mineral training samples (thousands)"), ax.invert_yaxis(), ax.grid(axis="y", visible=False)
save(fig, "chart_sources.png")

cc = mm["class_counts"]
fig, ax = plt.subplots(figsize=(8, 3))
order = sorted(cc, key=cc.get, reverse=True)
ax.bar(order, [cc[k] / 1000 for k in order], color=ORANGE, width=0.6)
ax.set_ylabel("Samples (thousands)"), ax.tick_params(axis="x", rotation=40, labelsize=8), ax.grid(axis="x", visible=False)
save(fig, "chart_classes.png")

r2 = metrics["metrics"]["agri_val"]["r2"]
fig, ax = plt.subplots(figsize=(8, 2.6))
ax.bar(list(r2), list(r2.values()), color=BLUE, width=0.55)
for i, k in enumerate(r2):
    ax.text(i, r2[k] + 0.003, f"{r2[k]:.3f}", ha="center", fontsize=8)
ax.axhline(0.95, color=INK2, lw=1, ls="--")
ax.text(5.45, 0.953, "gate 0.95 (core: Cab, Cw, LAI)", ha="right", fontsize=7, color=INK2)
ax.set_ylim(0.85, 1.01), ax.set_ylabel("R² vs LUT inversion"), ax.grid(axis="x", visible=False)
save(fig, "chart_agri_r2.png")

# prototype -> deployed model on real EMIT never trained on (prototype measured on the same rows, 21 Oct 2026)
G = card["gates"]
fig, axs = plt.subplots(1, 2, figsize=(9, 2.9))
for ax, metric, title in ((axs[0], "abundance_mae", "Abundance error vs SMA"), (axs[1], "quality_mae", "Quality error vs classical")):
    proto = {"abundance_mae": 0.0782, "quality_mae": 0.0125}[metric]
    vals = [("prototype, owner's scene", proto, INK2), (f"{card['version']}, owner's scene", G[f"mineral_real.{metric}"]["value"], BLUE),
            (f"{card['version']}, Death Valley", G[f"mineral_real_holdout.{metric}"]["value"], AQUA)]
    ax.bar([v[0] for v in vals], [v[1] for v in vals], color=[v[2] for v in vals], width=0.55)
    for i, v in enumerate(vals):
        ax.text(i, v[1], f"{v[1]:.4f}", ha="center", va="bottom", fontsize=8)
    ax.axhline(0.05, color=INK2, lw=1, ls="--")
    ax.set_title(title, fontsize=9), ax.tick_params(axis="x", labelsize=7, rotation=12), ax.grid(axis="x", visible=False)
axs[0].set_ylabel("MAE (lower is better)")
fig.tight_layout()
save(fig, "chart_prototype_vs_model.png")

fig, ax = plt.subplots(figsize=(8, 2.4))
abl = [("prototype: old data, 275 inputs", 0.0782, INK2), ("new data, 275 inputs", 0.00807, BLUE), ("new data, 530 inputs", 0.00631, AQUA)]
ax.barh([a[0] for a in abl], [a[1] for a in abl], color=[a[2] for a in abl], height=0.5)
for i, a in enumerate(abl):
    ax.text(a[1], i, f" {a[1]:.4f}", va="center", fontsize=8)
ax.set_xlabel("Abundance error on the owner's real scene (6,000-step trials; prototype = the earlier deployed network)")
ax.invert_yaxis(), ax.grid(axis="y", visible=False)
save(fig, "chart_ablation.png")
print("figures written to", OUT)
