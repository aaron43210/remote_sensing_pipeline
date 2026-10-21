"""
Map-level proof that HydraNet (ONNX) produces the same maps as the classical method.

    python -m verify_maps --model artifacts/models/v1 --data artifacts/data

Runs hydra.maps (the exact code path of the inference service) twice per
scene, once with the ONNX network and once with the classical method, and
compares the maps pixel by pixel:

    * synthetic EMIT scenes (fields, bare minerals, water, roads)  -> mineral + agriculture
    * REAL Landsat 8/9 scenes over the Central Valley              -> thermal

Writes <model>/map_verification.json and .md; exit code 2 if a gate fails.
"""

import argparse
import json
import time
from pathlib import Path

import numpy as np

from hydra import maps, spec, synthetic
from hydra.classical.agri_lut import LUTInverter
from hydra.runtime import HydraRuntime

MAP_GATES = {
    "mineral_class": ("pixel_agreement", 0.98),
    "mineral_abundance": ("mae", 0.03),
    "mineral_quality": ("mae", 0.03),
    # Whole-scene agriculture: error as a fraction of the parameter's physical range.
    # (R² inside one scene is variance-limited: a field's LAI varies by ~1, the
    # training range by 10, so the same error gives a far lower R². It is still reported.)
    "agri_Cab": ("nmae", 0.05), "agri_Cw": ("nmae", 0.05), "agri_LAI": ("nmae", 0.05),
    "thermal_global_class": ("pixel_agreement", 0.99),
    "thermal_uhi_class": ("pixel_agreement", 0.99),
    "thermal_local_anomaly": ("pixel_agreement", 0.99),
    "thermal_hotspots": ("pixel_agreement", 0.99),
}
# Every thermal map of EVERY real Landsat scene, not only the average: one hard scene
# (Tulare farms, hotspots near the p95 threshold) must not hide behind five easy ones.
REAL_SCENE_FLOOR = 0.98

# Real scenes: (name, bbox, date window). Several land covers and seasons.
LANDSAT_SCENES = [
    ("fresno_city", [-119.85, 36.70, -119.75, 36.78], ("2024-06-01", "2024-09-30")),
    ("fresno_winter", [-119.85, 36.70, -119.75, 36.78], ("2023-12-01", "2024-02-28")),
    ("bakersfield_oil", [-119.10, 35.35, -119.00, 35.43], ("2024-06-01", "2024-08-31")),
    ("sacramento", [-121.55, 38.52, -121.45, 38.60], ("2024-07-01", "2024-09-30")),
    ("tulare_farms", [-119.40, 36.15, -119.30, 36.23], ("2024-05-01", "2024-07-31")),
    ("modesto", [-121.05, 37.60, -120.95, 37.68], ("2024-07-01", "2024-09-30")),
]


def _combine(per_scene):
    """Pixel-weighted aggregate of per-scene agreement dicts."""
    out = {}
    for layer in {k for s in per_scene for k in s}:
        rows = [s[layer] for s in per_scene if layer in s]
        n = sum(r["n"] for r in rows)
        agg = {"n": n, "scenes": len(rows)}
        for key in ("pixel_agreement", "mae", "r2"):
            vals = [(r[key], r["n"]) for r in rows if r.get(key) is not None]
            if vals:
                agg[key] = sum(v * w for v, w in vals) / sum(w for _, w in vals)
                agg[f"{key}_worst_scene"] = (min if key != "mae" else max)(v for v, _ in vals)
        out[layer] = agg
    return out


def verify_emit(rt, lut, n_scenes, seed):
    per_scene, times = [], []
    for i in range(n_scenes):
        cube, wl, good, georef, land = synthetic.scene(seed + i)
        masks = maps.scene_masks(cube, wl)
        t0 = time.time()
        nn = {**maps.hydra_mineral(rt, cube, wl, masks == 3), **maps.hydra_agri(rt, cube, wl, masks == 2)}
        t_nn = time.time() - t0
        t0 = time.time()
        ref = {**maps.classical_mineral(cube, wl, masks == 3), **maps.classical_agri(lut, cube, wl, masks == 2)}
        t_ref = time.time() - t0
        a = maps.agreement(nn, ref)
        a.pop("mineral_confidence", None)
        a = {k: v for k, v in a.items() if not k.endswith("_std")}
        per_scene.append(a)
        times.append({"nn_s": round(t_nn, 3), "classical_s": round(t_ref, 3), "pixels": int(masks.size)})
    return per_scene, times


def verify_landsat(rt):
    from hs_common.sources import landsat
    per_scene, info = [], []
    for name, bbox, (d0, d1) in LANDSAT_SCENES:
        try:
            item = landsat.search(bbox, d0, d1)
            if item is None:
                info.append({"scene": name, "skipped": "no clear scene"})
                continue
            st, qa, meta = landsat.read_window(item, bbox)
            lst = landsat.to_celsius(st, qa, meta["scale"], meta["offset"])
            nn, ref = maps.hydra_thermal(rt, lst), maps.classical_thermal(lst)
            a = maps.agreement(nn, ref)
            a.pop("thermal_lst", None)
            per_scene.append(a)
            info.append({"scene": name, "landsat_id": meta["scene_id"], "acquired_at": meta["acquired_at"],
                         "shape": list(lst.shape), "lst_c_p5_p95": [round(float(np.nanpercentile(lst, 5)), 1),
                                                                    round(float(np.nanpercentile(lst, 95)), 1)],
                         "uhi_intensity_nn_c": round(nn["uhi_intensity_c"], 2),
                         "uhi_intensity_classical_c": round(ref["uhi_intensity_c"], 2),
                         "agreement": {k: v.get("pixel_agreement") for k, v in a.items()}})
        except Exception as e:                      # network hiccup: report, do not hide
            info.append({"scene": name, "error": str(e)[:300]})
    return per_scene, info


def gate(agg):
    for layer, a in agg.items():                       # normalised MAE for agriculture layers
        p = layer[5:] if layer.startswith("agri_") else None
        if p in spec.AGRI_RANGES and "mae" in a:
            lo, hi = spec.AGRI_RANGES[p]
            a["nmae"] = a["mae"] / (hi - lo)
    res = {}
    for layer, (metric, thr) in MAP_GATES.items():
        v = agg.get(layer, {}).get(metric)
        ok = None if v is None else (v <= thr if metric in ("mae", "nmae") else v >= thr)
        res[layer] = {"metric": metric, "value": None if v is None else round(v, 5),
                      "threshold": thr, "passed": ok}
        if layer.startswith("agri_") and agg.get(layer, {}).get("r2") is not None:
            res[layer]["r2_within_scene"] = round(agg[layer]["r2"], 4)
    return res


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="artifacts/models/v1")
    ap.add_argument("--data", default="artifacts/data")
    ap.add_argument("--emit-scenes", type=int, default=8)
    ap.add_argument("--seed", type=int, default=1000)
    ap.add_argument("--no-landsat", action="store_true")
    a = ap.parse_args(argv)
    model_dir = Path(a.model)
    rt = HydraRuntime(model_dir / "hydra.onnx")
    lut = LUTInverter.load(Path(a.data) / "agri_lut.npz")

    emit_scenes, emit_times = verify_emit(rt, lut, a.emit_scenes, a.seed)
    th_scenes, th_info = ([], []) if a.no_landsat else verify_landsat(rt)
    agg = {**_combine(emit_scenes), **_combine(th_scenes)}
    gates = gate(agg)
    scene_values = [(s["scene"], k, v) for s in th_info for k, v in s.get("agreement", {}).items() if v is not None]
    if scene_values:
        worst = min(scene_values, key=lambda t: t[2])
        gates["thermal_real_scene_min"] = {"metric": "pixel_agreement", "value": round(worst[2], 5),
                                           "threshold": REAL_SCENE_FLOOR, "passed": worst[2] >= REAL_SCENE_FLOOR,
                                           "worst": f"{worst[0]}/{worst[1]}"}
    passed = all(g["passed"] for g in gates.values() if g["passed"] is not None)
    report = {"model_version": rt.version, "feature_spec_hash": spec.feature_spec_hash(),
              "gates": gates, "all_passed": passed, "aggregate": agg,
              "emit_synthetic": {"scenes": a.emit_scenes, "timing": emit_times},
              "landsat_real": th_info}
    (model_dir / "map_verification.json").write_text(json.dumps(report, indent=2))

    lines = [f"# Map verification: HydraNet {rt.version} vs classical method", "",
             "| Layer | Metric | HydraNet vs classical | Gate | Result |", "|---|---|---|---|---|"]
    for layer, g in gates.items():
        res = "n/a" if g["passed"] is None else ("PASS" if g["passed"] else "FAIL")
        lines.append(f"| {layer} | {g['metric']} | {g['value']} | {g['threshold']} | {res} |")
    lines += ["", "## Real Landsat scenes", "", "| Scene | Landsat ID | Date | Global | UHI | Local | Hotspots |",
              "|---|---|---|---|---|---|---|"]
    for s in th_info:
        if "agreement" in s:
            ag = s["agreement"]
            lines.append(f"| {s['scene']} | {s['landsat_id']} | {s['acquired_at'][:10]} | "
                         f"{ag.get('thermal_global_class')} | {ag.get('thermal_uhi_class')} | "
                         f"{ag.get('thermal_local_anomaly')} | {ag.get('thermal_hotspots')} |")
        else:
            lines.append(f"| {s['scene']} | - | - | {s.get('skipped') or s.get('error')} | | | |")
    (model_dir / "map_verification.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    return 0 if passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
