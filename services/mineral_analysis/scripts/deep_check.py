"""
Deep verification of:
1. services/mineral_analysis/data/usgs_library/*.csv
2. services/mineral_analysis/output/training_set/training_sample.csv
3. services/mineral_analysis/output/dataset/dataset_sample.csv
"""
import os
import csv
import json
import numpy as np
from pathlib import Path

BASE = Path(r"C:\Users\sanan\OneDrive\Documents\Thermal Remote Sensing\Project\remote_sensing_pipeline\services\mineral_analysis")
USGS_DIR = BASE / "data" / "usgs_library"
TRAINING_CSV = BASE / "output" / "training_set" / "training_sample.csv"
DATASET_CSV = BASE / "output" / "dataset" / "dataset_sample.csv"

def run_checks():
    results = {"usgs": {}, "training": {}, "dataset": {}, "comparison": {}, "issues": []}

    # 1. USGS Library
    minerals = sorted([p.stem for p in USGS_DIR.glob("*.csv")])
    results["usgs"]["minerals"] = minerals
    results["usgs"]["count"] = len(minerals)
    
    spectra = {}
    for m in minerals:
        path = USGS_DIR / f"{m}.csv"
        with open(path, "r", encoding="utf-8") as f:
            lines = [l.strip() for l in f if l.strip()]
        
        meta = {}
        data = []
        for l in lines:
            if l.startswith("#"):
                k, sep, v = l[1:].partition(":")
                if sep:
                    meta[k.strip().lower()] = v.strip()
            elif l.startswith("wavelength"):
                header = l.split(",")
            else:
                data.append([float(x) for x in l.split(",")])
        
        arr = np.array(data)
        wl = arr[:, 0]
        refl = arr[:, 1]
        spectra[m] = (wl, refl)

        checks = {
            "n_points": len(wl),
            "wl_min": float(wl[0]),
            "wl_max": float(wl[-1]),
            "wl_step": float(np.diff(wl).mean()),
            "wl_strictly_increasing": bool(np.all(np.diff(wl) > 0)),
            "refl_min": float(refl.min()),
            "refl_max": float(refl.max()),
            "has_nan": bool(np.isnan(arr).any()),
            "has_inf": bool(np.isinf(arr).any()),
            "meta_name": meta.get("name"),
            "meta_unit": meta.get("wavelength_unit"),
        }
        results["usgs"][m] = checks
        
        if checks["meta_name"] != m:
            results["issues"].append(f"USGS {m}: meta name {checks['meta_name']} != filename {m}")
        if checks["refl_min"] < 0 or checks["refl_max"] > 1:
            results["issues"].append(f"USGS {m}: reflectance out of [0, 1]: [{checks['refl_min']}, {checks['refl_max']}]")
        if not checks["wl_strictly_increasing"]:
            results["issues"].append(f"USGS {m}: wavelengths not strictly increasing")

    # Cross check correlations
    for i, m1 in enumerate(minerals):
        for j, m2 in enumerate(minerals):
            if j <= i:
                continue
            r1 = spectra[m1][1]
            r2 = spectra[m2][1]
            diff = np.max(np.abs(r1 - r2))
            if diff < 1e-6:
                results["issues"].append(f"USGS {m1} and {m2} are IDENTICAL!")

    # 2. Training Sample CSV
    with open(TRAINING_CSV, "r", encoding="utf-8") as f:
        reader = csv.reader(f)
        tr_header = next(reader)
        tr_rows = list(reader)

    tr_data = np.array([[float(x) if ci != tr_header.index("dominant") else x for ci, x in enumerate(row)] for row in tr_rows], dtype=object)
    results["training"]["rows"] = len(tr_rows)
    results["training"]["cols"] = len(tr_header)

    tr_cr = [c for c in tr_header if c.startswith("cr_")]
    tr_depth = [c for c in tr_header if c.startswith("depth_")]
    tr_sam = [c for c in tr_header if c.startswith("sam_")]
    tr_abund = [c for c in tr_header if c.startswith("abund_")]

    ab_indices = [tr_header.index(c) for c in tr_abund]
    ab_vals = tr_data[:, ab_indices].astype(float)
    ab_sums = ab_vals.sum(axis=1)
    results["training"]["abund_sum_min"] = float(ab_sums.min())
    results["training"]["abund_sum_max"] = float(ab_sums.max())
    results["training"]["abund_sum_mean"] = float(ab_sums.mean())
    if np.any(np.abs(ab_sums - 1.0) > 1e-3):
        results["issues"].append(f"Training: abundance sums deviate from 1.0: min={ab_sums.min():.4f}, max={ab_sums.max():.4f}")

    # Check dominant agreement with argmax abundance
    dom_idx = tr_header.index("dominant")
    dom_labels = tr_data[:, dom_idx]
    mineral_from_abund = [tr_abund[np.argmax(ab_vals[i])].replace("abund_", "") for i in range(len(tr_rows))]
    disagreements = sum(1 for d, m in zip(dom_labels, mineral_from_abund) if d != m)
    results["training"]["dominant_argmax_disagreements"] = disagreements
    if disagreements > 0:
        results["issues"].append(f"Training: {disagreements} rows where 'dominant' does not match argmax(abund_*)")

    # 3. Dataset Sample CSV
    with open(DATASET_CSV, "r", encoding="utf-8") as f:
        reader = csv.reader(f)
        ds_header = next(reader)
        ds_rows = list(reader)

    results["dataset"]["rows"] = len(ds_rows)
    results["dataset"]["cols"] = len(ds_header)

    ds_cr = [c for c in ds_header if c.startswith("cr_")]
    ds_depth = [c for c in ds_header if c.startswith("depth_")]
    ds_sam = [c for c in ds_header if c.startswith("sam_")]
    ds_abund = [c for c in ds_header if c.startswith("abund_")]

    ds_ab_indices = [ds_header.index(c) for c in ds_abund]
    ds_ab_vals = np.array([[float(row[ci]) for ci in ds_ab_indices] for row in ds_rows])
    ds_ab_sums = ds_ab_vals.sum(axis=1)
    results["dataset"]["abund_sum_min"] = float(ds_ab_sums.min())
    results["dataset"]["abund_sum_max"] = float(ds_ab_sums.max())
    results["dataset"]["abund_sum_mean"] = float(ds_ab_sums.mean())

    # Check label_index vs label_name
    li_idx = ds_header.index("label_index")
    ln_idx = ds_header.index("label_name")
    for r in ds_rows:
        l_idx = int(r[li_idx])
        l_name = r[ln_idx]
        if l_idx < 0 or l_idx >= len(minerals) or minerals[l_idx] != l_name:
            results["issues"].append(f"Dataset: label_index {l_idx} does not correspond to label_name '{l_name}'")
            break

    # 4. Comparison
    results["comparison"]["cr_match"] = (tr_cr == ds_cr)
    results["comparison"]["depth_match"] = (tr_depth == ds_depth)
    results["comparison"]["sam_match"] = (tr_sam == ds_sam)
    results["comparison"]["abund_match"] = (tr_abund == ds_abund)
    results["comparison"]["cols_only_in_training"] = sorted(list(set(tr_header) - set(ds_header)))
    results["comparison"]["cols_only_in_dataset"] = sorted(list(set(ds_header) - set(tr_header)))

    print(json.dumps(results, indent=2))
    return results

if __name__ == "__main__":
    run_checks()
