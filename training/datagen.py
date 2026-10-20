"""
Training/evaluation sets for HydraNet, every label produced by the classical teacher.

    python -m datagen --out artifacts/data --data-root ../..        (all three)
    python -m datagen --only mineral

Outputs (npz, float32) + a manifest with sizes, seeds and spec hash:
    mineral_train / mineral_val   synthetic: owner's 62,400 (seed 0) + coverage set
    mineral_real                  dataset_sample.csv rows (real EMIT, teacher labels as published)
    agri_lut                      the LUT the classical inversion uses
    agri_train / agri_val         PROSAIL spectra (+ nuisance variation, noise) -> LUT labels
    agri_csv                      the 10,000 CSV rows -> LUT labels (plus true params)
    thermal_train / thermal_val   synthetic LST scenes -> detector labels
"""

import argparse
import json
import os
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import ndimage

from hydra import spec
from hydra.classical import agri_lut, thermal as thermal_rules
from hydra.classical.library import aligned_library
from hydra.classical.mineral import MineralTeacher
from hydra.features import agri as agri_features
from hydra.features.mineral import MineralFeatures
from hydra.features.thermal import raster_features
from hydra.synthetic import synthetic_lst

# ── Mineral ──────────────────────────────────────────────────────────────────

MIX_K_PROBS = (0.30, 0.35, 0.25, 0.10)


def simulate_minerals(endmembers, n, rng, k_probs=MIX_K_PROBS, brightness=(0.6, 1.2),
                      noise=(0.003, 0.015)):
    """The owner's forward model (rtm_inversion/synth.simulate), parameterised."""
    n_em = endmembers.shape[0]
    ks = rng.choice(np.arange(1, len(k_probs) + 1), size=n, p=np.asarray(k_probs))
    ab = np.zeros((n, n_em))
    for i, k in enumerate(ks):
        members = rng.choice(n_em, size=int(k), replace=False)   # draw order = upstream
        ab[i, members] = rng.dirichlet(np.ones(int(k)))
    s = ab @ endmembers
    s *= rng.uniform(*brightness, size=(n, 1))
    sigma = rng.uniform(*noise, size=(n, 1))          # drawn before the noise, as upstream
    s += rng.normal(0.0, 1.0, s.shape) * sigma
    return np.clip(s, 0.001, 1.5), ab


def owner_training_set(endmembers):
    """Exactly synth.generate(seed=0): 60,000 mixtures + 2,400 pure, permuted."""
    rng = np.random.default_rng(0)
    s, ab = simulate_minerals(endmembers, 60_000, rng)
    s = s.astype(np.float32)
    pure = np.zeros((12 * 200, 12), np.float32)
    for m in range(12):
        pure[m * 200:(m + 1) * 200, m] = 1
    ps = pure @ endmembers
    ps *= rng.uniform(0.6, 1.2, size=(ps.shape[0], 1))
    ps += rng.normal(0, 1, ps.shape) * rng.uniform(0.003, 0.015, size=(ps.shape[0], 1))
    np.clip(ps, 0.001, 1.5, out=ps)
    s = np.vstack([s, ps.astype(np.float32)])
    order = rng.permutation(s.shape[0])
    return s[order]


def _vegetation_spectra(n, rng, wl):
    params = agri_lut.sample_params(n, rng)
    rows = np.arange(0, 2101)                          # 400..2500 nm
    full = agri_lut.simulate(params, rows=rows)
    grid = np.arange(400, 2501, dtype=float)
    return np.vstack([np.interp(wl, grid, r) for r in full])


def _background_spectra(n, rng, wl):
    """Water, dark shadow and smooth soil-like curves: things that are not minerals."""
    kind = rng.integers(0, 3, n)
    x = (wl - 380.0) / 2120.0
    out = np.empty((n, wl.size))
    for i, k in enumerate(kind):
        if k == 0:      # water: bright blue, near zero in NIR/SWIR
            out[i] = rng.uniform(0.02, 0.08) * np.exp(-x * rng.uniform(6, 14)) + rng.uniform(0, 0.01)
        elif k == 1:    # shadow / asphalt: dark, flat
            out[i] = rng.uniform(0.02, 0.10) * (1 + rng.uniform(-0.2, 0.3) * x)
        else:           # soil: rising then gently falling
            a, b = rng.uniform(0.1, 0.4), rng.uniform(0.5, 1.5)
            out[i] = a * (0.4 + b * x - 0.6 * x ** 2) + rng.uniform(0, 0.05)
    return np.clip(out, 0.001, 1.0)


def coverage_set(endmembers, wl, n, rng):
    """
    Spectra the owner's set never shows the teacher: darker and brighter mixtures,
    vegetation, water, soil, and minerals partly covered by them. The teacher
    labels every one, so the network learns what SAM/SMA do on real scenes.
    """
    n_mix = n // 2
    s1, _ = simulate_minerals(endmembers, n_mix, rng, brightness=(0.15, 1.6), noise=(0.001, 0.02))
    n_bg = n - n_mix
    veg = _vegetation_spectra(n_bg // 2, rng, wl)
    bg = _background_spectra(n_bg - n_bg // 2, rng, wl)
    others = np.vstack([veg, bg])
    # half of the non-mineral spectra get a mineral component mixed in
    m, _ = simulate_minerals(endmembers, others.shape[0], rng, brightness=(0.3, 1.3))
    f = rng.uniform(0, 1, (others.shape[0], 1)) * (rng.random((others.shape[0], 1)) < 0.5)
    others = (1 - f) * others + f * m
    others += rng.normal(0, 1, others.shape) * rng.uniform(0.001, 0.01, (others.shape[0], 1))
    return np.clip(np.vstack([s1, others]), 0.001, 1.5)


def near_tie_set(endmembers, n, rng):
    """
    Spectra where SAM's choice is a near-tie: quartz and gypsum share half their
    library spectrum, so dark, featureless real pixels sit 1e-3 rad from both.
    Mixtures dominated by those two, at real-scene brightness, with a little of
    the others, teach the network where SAM's decision boundary really is.
    """
    q, g = spec.MINERALS.index("quartz"), spec.MINERALS.index("gypsum")
    ab = np.zeros((n, endmembers.shape[0]))
    t = rng.uniform(0, 1, n)
    ab[:, q], ab[:, g] = t, 1 - t
    others = rng.dirichlet(np.ones(endmembers.shape[0]), n) * rng.uniform(0, 0.25, (n, 1))
    ab = ab * (1 - others.sum(1, keepdims=True)) + others
    s = (ab @ endmembers) * rng.uniform(0.25, 0.7, (n, 1))
    s += rng.normal(0, 1, s.shape) * rng.uniform(0.001, 0.01, (n, 1))
    return np.clip(s, 0.001, 1.5)


def build_mineral(out, data_root, n_coverage, seed, n_ties=40_000):
    feats = MineralFeatures()
    teacher = MineralTeacher(feats)
    wl = feats.wavelengths
    _, em = aligned_library(wl)
    rng = np.random.default_rng(seed)
    owner = owner_training_set(em)
    spectra = np.vstack([owner, coverage_set(em, wl, n_coverage, rng), near_tie_set(em, n_ties, rng)])
    source = np.r_[np.zeros(62_400, np.int8), np.ones(spectra.shape[0] - 62_400, np.int8)]
    x = feats.transform(spectra)

    owner_csv = Path(data_root) / "Mineral" / "training_sample.csv"
    if owner_csv.exists():       # the regenerated set must BE the owner's set
        ref = pd.read_csv(owner_csv, nrows=2000)[spec.mineral_feature_names()].values
        diff = float(np.nanmax(np.abs(x[:2000] - ref)))
        if diff > 1e-4:
            raise RuntimeError(f"owner training set not reproduced (max diff {diff})")
    lab = teacher.label(spectra, x)
    ok = (lab["class_index"] >= 0) & np.isfinite(lab["abundances"]).all(1) & np.isfinite(x).all(1)
    x, spectra, source = x[ok], spectra[ok], source[ok]
    lab = {k: v[ok] for k, v in lab.items()}
    idx = rng.permutation(x.shape[0])
    n_val = max(5000, x.shape[0] // 10)
    for name, sel in (("mineral_val", idx[:n_val]), ("mineral_train", idx[n_val:])):
        np.savez_compressed(out / f"{name}.npz", x=x[sel], y_class=lab["class_index"][sel],
                            y_abund=lab["abundances"][sel].astype(np.float32),
                            y_quality=lab["quality"][sel].astype(np.float32), source=source[sel])

    csv = Path(data_root) / "Mineral" / "dataset_sample.csv"
    if csv.exists():
        d = pd.read_csv(csv)
        xr = d[spec.mineral_feature_names()].values.astype(np.float32)
        cls = np.array([spec.MINERALS.index(n) for n in d["label_name"]])
        np.savez_compressed(out / "mineral_real.npz", x=xr, y_class=cls,
                            y_abund=d[[f"abund_{m}" for m in spec.MINERALS]].values.astype(np.float32),
                            y_quality=d["quality"].values.astype(np.float32))
    counts = np.bincount(lab["class_index"], minlength=13)
    return {"n": int(x.shape[0]), "n_val": int(n_val), "class_counts":
            dict(zip(spec.MINERAL_CLASSES, map(int, counts)))}


# ── Agriculture ──────────────────────────────────────────────────────────────

def _simulate_chunk(args):
    params, fixed = args
    return agri_lut.simulate(params, fixed=fixed)


def simulate_parallel(params, fixed_rows=None, workers=None):
    """PROSAIL over many rows on all cores. fixed_rows: list of per-row overrides or None."""
    workers = workers or os.cpu_count()
    chunks = np.array_split(np.arange(len(params)), workers * 4)
    if fixed_rows is None:
        jobs = [(params[c], None) for c in chunks]
        with ProcessPoolExecutor(workers) as ex:
            return np.vstack(list(ex.map(_simulate_chunk, jobs)))
    out = np.empty((len(params), len(spec.AGRI_BANDS_NM)), np.float32)
    groups = {}
    for i, f in enumerate(fixed_rows):
        groups.setdefault(tuple(sorted(f.items())), []).append(i)
    jobs, order = [], []
    for key, rows in groups.items():
        for c in np.array_split(np.array(rows), max(1, len(rows) // 500)):
            jobs.append((params[c], dict(key)))
            order.append(c)
    with ProcessPoolExecutor(workers) as ex:
        for c, res in zip(order, ex.map(_simulate_chunk, jobs)):
            out[c] = res
    return out


def nuisance_fixed(n, rng):
    """Soil, leaf structure and sun-angle variation the LUT does not model (real scenes have it)."""
    out = []
    for _ in range(n):
        out.append({"n": round(float(rng.uniform(1.2, 2.2)), 1),
                    "psoil": round(float(rng.uniform(0, 1)), 1),
                    "rsoil": round(float(rng.uniform(0.6, 1.4)), 1),
                    "tts": float(rng.choice([20.0, 30.0, 40.0, 50.0]))})
    return out


def build_agri(out, data_root, n_lut, n_train, seed):
    rng = np.random.default_rng(seed)
    t0 = time.time()
    lut_params = agri_lut.sample_params(n_lut, rng)
    lut_spectra = simulate_parallel(lut_params)
    lut = agri_lut.LUTInverter(lut_spectra, lut_params)
    lut.save(out / "agri_lut.npz")

    n_fixed = n_train // 2
    params = agri_lut.sample_params(n_train, rng)
    s_fixed = simulate_parallel(params[:n_fixed])
    s_var = simulate_parallel(params[n_fixed:], nuisance_fixed(n_train - n_fixed, rng))
    s = np.vstack([s_fixed, s_var])
    gain = rng.uniform(0.95, 1.05, (n_train, 1))
    sigma = rng.uniform(0.0, 0.008, (n_train, 1))
    s = np.clip(s * gain + rng.normal(0, 1, s.shape) * sigma, 0.0, 1.0).astype(np.float32)
    lab = lut.invert(s)
    idx = rng.permutation(n_train)
    n_val = n_train // 10
    for name, sel in (("agri_val", idx[:n_val]), ("agri_train", idx[n_val:])):
        np.savez_compressed(out / f"{name}.npz", x=s[sel], y_params=lab["params"][sel].astype(np.float32),
                            y_std=lab["std"][sel].astype(np.float32), true_params=params[sel].astype(np.float32))

    csv = Path(data_root) / "Agriculture" / "RTM_NN_TRAINING_DATASET_WAVELENGTHS.csv"
    if csv.exists():
        d = pd.read_csv(csv)
        xc = d[agri_features.dataset_columns()].values.astype(np.float32)
        lc = lut.invert(xc)
        np.savez_compressed(out / "agri_csv.npz", x=xc, y_params=lc["params"].astype(np.float32),
                            y_std=lc["std"].astype(np.float32),
                            true_params=d[list(spec.AGRI_PARAMS)].values.astype(np.float32))
    return {"n_lut": n_lut, "n_train": n_train, "seconds": round(time.time() - t0, 1)}


# ── Thermal ──────────────────────────────────────────────────────────────────

def thermal_samples(lst, rng, per_scene):
    f, valid, _ = raster_features(lst)
    t = thermal_rules.teacher_maps(lst)
    flat_valid = np.flatnonzero(valid.ravel())
    hot = np.flatnonzero((t["hot_p95"] | t["local_anomaly"] | (t["global_class"] != 1)).ravel() & valid.ravel())
    pick = np.unique(np.r_[rng.choice(flat_valid, min(per_scene, flat_valid.size), replace=False),
                           rng.choice(hot, min(per_scene // 4, hot.size), replace=False) if hot.size else []]).astype(int)
    return (f[pick], t["global_class"].ravel()[pick], t["uhi_class"].ravel()[pick],
            t["hot_p95"].ravel()[pick], t["local_anomaly"].ravel()[pick])


def build_thermal(out, n_scenes, per_scene, seed):
    rng = np.random.default_rng(seed)
    parts = [thermal_samples(synthetic_lst(rng), rng, per_scene) for _ in range(n_scenes)]
    cols = [np.concatenate([p[i] for p in parts]) for i in range(5)]
    n_val_scenes = max(1, n_scenes // 10)
    split = sum(len(p[0]) for p in parts[:n_val_scenes])          # split by SCENE, not pixel
    for name, sl in (("thermal_val", slice(0, split)), ("thermal_train", slice(split, None))):
        np.savez_compressed(out / f"{name}.npz", x=cols[0][sl], y_global=cols[1][sl].astype(np.int64),
                            y_uhi=cols[2][sl].astype(np.int64), y_hot=cols[3][sl].astype(np.float32),
                            y_local=cols[4][sl].astype(np.float32))
    return {"n": int(len(cols[0])), "scenes": n_scenes, "val_scenes": n_val_scenes}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="artifacts/data")
    ap.add_argument("--data-root", default=str(Path(__file__).resolve().parents[2]))
    ap.add_argument("--only", choices=["mineral", "agri", "thermal"])
    ap.add_argument("--mineral-coverage", type=int, default=100_000)
    ap.add_argument("--agri-lut", type=int, default=100_000)
    ap.add_argument("--agri-train", type=int, default=200_000)
    ap.add_argument("--thermal-scenes", type=int, default=400)
    ap.add_argument("--thermal-per-scene", type=int, default=3000)
    ap.add_argument("--seed", type=int, default=42)
    a = ap.parse_args(argv)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    manifest_path = out / "manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    manifest.update({"spec_hash": spec.feature_spec_hash(), "seed": a.seed})
    if a.only in (None, "mineral"):
        manifest["mineral"] = build_mineral(out, a.data_root, a.mineral_coverage, a.seed)
        print("mineral", manifest["mineral"], flush=True)
    if a.only in (None, "agri"):
        manifest["agri"] = build_agri(out, a.data_root, a.agri_lut, a.agri_train, a.seed + 1)
        print("agri", manifest["agri"], flush=True)
    if a.only in (None, "thermal"):
        manifest["thermal"] = build_thermal(out, a.thermal_scenes, a.thermal_per_scene, a.seed + 2)
        print("thermal", manifest["thermal"], flush=True)
    manifest_path.write_text(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
