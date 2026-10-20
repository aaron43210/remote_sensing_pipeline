"""
Train HydraNet to reproduce the classical maps, export ONNX, verify the ONNX file.

    python -m train --data artifacts/data --out artifacts/models --version v1

Writes <out>/<version>/hydra.onnx, model_card.json, metrics.json. The model is
selected on validation agreement with the teacher; the gates in GATES are then
evaluated on the exported ONNX file (the deployed artifact), not on PyTorch.
"""

import argparse
import json
import math
import subprocess
import time
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F

from hydra import spec
from hydra_model import HydraExport, HydraNet

# Agreement with the classical method that a model must reach to be promoted.
GATES = {
    "mineral_val.class_agreement": 0.98,
    "mineral_val.abundance_mae": 0.03,
    "mineral_val.quality_mae": 0.03,
    "mineral_real.class_agreement": 0.97,  # real EMIT pixels: same class as SAM
    "mineral_real.abundance_mae": 0.06,    # domain-gap abundance check
    "agri_val.r2_min_core": 0.95,          # Cab, Cw, LAI vs the LUT inversion
    "agri_csv.r2_min_core": 0.95,
    "thermal_val.global_agreement": 0.99,
    "thermal_val.uhi_agreement": 0.99,
    "thermal_val.hot_agreement": 0.99,
    "thermal_val.local_agreement": 0.99,
}
CORE_AGRI = ("Cab", "Cw", "LAI")


def load(data, name):
    path = Path(data) / f"{name}.npz"
    if not path.exists():
        return None
    with np.load(path) as f:
        return {k: f[k] for k in f.files}


def norm_stats(x):
    return np.nanmean(x, axis=0), np.nanstd(x, axis=0)


# ── Metrics (shared by torch validation and ONNX verification) ──────────────

def r2(pred, ref):
    ss = ((ref - ref.mean(0)) ** 2).sum(0)
    return 1 - ((pred - ref) ** 2).sum(0) / np.maximum(ss, 1e-12)


def mineral_metrics(out, d):
    cls = out["mineral_class_probs"].argmax(1)
    m = {"class_agreement": float((cls == d["y_class"]).mean()),
         "abundance_mae": float(np.abs(out["mineral_abundance"] - d["y_abund"]).mean()),
         "dominant_abundance_agreement": float((out["mineral_abundance"].argmax(1) == d["y_abund"].argmax(1)).mean()),
         "quality_mae": float(np.nanmean(np.abs(out["mineral_quality"] - d["y_quality"]))),
         "n": int(len(cls))}
    per = {}
    for c, name in enumerate(spec.MINERAL_CLASSES):
        sel = d["y_class"] == c
        if sel.any():
            per[name] = {"n": int(sel.sum()), "agreement": float((cls[sel] == c).mean())}
    m["per_class"] = per
    return m


def agri_metrics(out, d):
    rr = r2(out["agri_params"], d["y_params"])
    m = {"r2": dict(zip(spec.AGRI_PARAMS, map(float, rr))),
         "mae": dict(zip(spec.AGRI_PARAMS, map(float, np.abs(out["agri_params"] - d["y_params"]).mean(0)))),
         "r2_min_core": float(min(rr[spec.AGRI_PARAMS.index(p)] for p in CORE_AGRI)),
         "std_r2": dict(zip(spec.AGRI_PARAMS, map(float, r2(out["agri_std"], d["y_std"])))),
         "n": int(len(d["x"]))}
    if "true_params" in d:      # how both methods compare to the simulation truth
        m["r2_nn_vs_truth"] = dict(zip(spec.AGRI_PARAMS, map(float, r2(out["agri_params"], d["true_params"]))))
        m["r2_classical_vs_truth"] = dict(zip(spec.AGRI_PARAMS, map(float, r2(d["y_params"], d["true_params"]))))
    return m


def thermal_metrics(out, d):
    return {"global_agreement": float((out["thermal_global_probs"].argmax(1) == d["y_global"]).mean()),
            "uhi_agreement": float((out["thermal_uhi_probs"].argmax(1) == d["y_uhi"]).mean()),
            "hot_agreement": float(((out["thermal_hot_prob"] > 0.5) == (d["y_hot"] > 0.5)).mean()),
            "local_agreement": float(((out["thermal_local_prob"] > 0.5) == (d["y_local"] > 0.5)).mean()),
            "n": int(len(d["x"]))}


METRICS = {"mineral": mineral_metrics, "agri": agri_metrics, "thermal": thermal_metrics}


def check_gates(metrics):
    results = {}
    for key, thr in GATES.items():
        ds, name = key.split(".")
        val = metrics.get(ds, {}).get(name)
        if val is None:
            results[key] = {"value": None, "threshold": thr, "passed": None}
            continue
        lower_is_better = name.endswith("_mae")
        ok = val <= thr if lower_is_better else val >= thr
        results[key] = {"value": round(val, 5), "threshold": thr, "passed": bool(ok)}
    return results


# ── Torch side ──────────────────────────────────────────────────────────────

@torch.no_grad()
def torch_predict(net, task, x, bs=8192):
    net.eval()
    outs = []
    for i in range(0, len(x), bs):
        xb = torch.from_numpy(np.nan_to_num(x[i:i + bs]).astype(np.float32))
        if task == "agri":
            p, s = net.agri_out(xb)
            outs.append({"agri_params": p, "agri_std": s})
        elif task == "mineral":
            c, a, q = net.mineral_out(xb)
            outs.append({"mineral_class_probs": c, "mineral_abundance": a, "mineral_quality": q})
        else:
            g, u, h, l = net.thermal_out(xb)
            outs.append({"thermal_global_probs": g, "thermal_uhi_probs": u,
                         "thermal_hot_prob": h, "thermal_local_prob": l})
    return {k: torch.cat([o[k] for o in outs]).numpy() for k in outs[0]}


def evaluate(predict, sets):
    res = {}
    for name, d in sets.items():
        if d is not None:
            task = name.split("_")[0]
            res[name] = METRICS[task](predict(task, d["x"]), d)
    return res


def selection_score(m):
    """One number to pick the best checkpoint: mean agreement across heads."""
    mv, av, tv = m["mineral_val"], m["agri_val"], m["thermal_val"]
    return np.mean([mv["class_agreement"], 1 - mv["abundance_mae"] * 5, av["r2_min_core"],
                    tv["global_agreement"], tv["uhi_agreement"], tv["hot_agreement"],
                    tv["local_agreement"]])


def train(args):
    torch.manual_seed(args.seed)
    np.random.seed(args.seed)
    torch.set_num_threads(args.threads)
    tr = {t: load(args.data, f"{t}_train") for t in ("agri", "mineral", "thermal")}
    val = {n: load(args.data, n) for n in ("agri_val", "agri_csv", "mineral_val", "mineral_real", "thermal_val")}

    net = HydraNet(arch=args.arch)
    net.agri_norm.set(*norm_stats(tr["agri"]["x"]))
    net.mineral_stem.norm.set(*norm_stats(tr["mineral"]["x"]))
    net.thermal_norm.set(*norm_stats(tr["thermal"]["x"]))
    span = net.agri_span.numpy()
    lo = net.agri_lo.numpy()

    tensors = {
        "agri": [torch.from_numpy(tr["agri"]["x"]),
                 torch.from_numpy((tr["agri"]["y_params"] - lo) / span),
                 torch.from_numpy(tr["agri"]["y_std"] / span)],
        "mineral": [torch.from_numpy(np.nan_to_num(tr["mineral"]["x"])),
                    torch.from_numpy(tr["mineral"]["y_class"].astype(np.int64)),
                    torch.from_numpy(np.clip(tr["mineral"]["y_abund"], 0, None)),
                    torch.from_numpy(np.nan_to_num(tr["mineral"]["y_quality"]))],
        "thermal": [torch.from_numpy(tr["thermal"]["x"]),
                    torch.from_numpy(tr["thermal"]["y_global"]), torch.from_numpy(tr["thermal"]["y_uhi"]),
                    torch.from_numpy(tr["thermal"]["y_hot"]), torch.from_numpy(tr["thermal"]["y_local"])],
    }
    ab = tensors["mineral"][2]
    tensors["mineral"][2] = ab / ab.sum(1, keepdim=True).clamp(min=1e-6)
    bs = {"agri": args.batch, "mineral": args.batch, "thermal": args.batch * 2}

    opt = torch.optim.AdamW(net.parameters(), lr=args.lr, weight_decay=1e-4)
    sched = torch.optim.lr_scheduler.OneCycleLR(opt, max_lr=args.lr, total_steps=args.steps, pct_start=0.05)
    best, best_state, history = -math.inf, None, []
    t0 = time.time()
    for step in range(1, args.steps + 1):
        net.train()
        losses = {}
        i = torch.randint(0, len(tensors["agri"][0]), (bs["agri"],))
        x, y, s = (t[i] for t in tensors["agri"])
        raw = net.agri_raw(x)
        n = len(spec.AGRI_PARAMS)
        pstd = F.softplus(raw[:, n:]) * 0.1
        losses["agri"] = F.mse_loss(torch.sigmoid(raw[:, :n]), y) + 0.25 * F.mse_loss(pstd, s)

        i = torch.randint(0, len(tensors["mineral"][0]), (bs["mineral"],))
        x, yc, ya, yq = (t[i] for t in tensors["mineral"])
        lc, la, lq = net.mineral_raw(x)
        losses["mineral"] = (F.cross_entropy(lc, yc, label_smoothing=0.0)
                             + 20 * F.mse_loss(F.softmax(la, 1), ya)
                             + 10 * F.mse_loss(torch.sigmoid(lq), yq))

        i = torch.randint(0, len(tensors["thermal"][0]), (bs["thermal"],))
        x, yg, yu, yh, yl = (t[i] for t in tensors["thermal"])
        r = net.thermal_raw(x)
        losses["thermal"] = (F.cross_entropy(r[:, 0:3], yg) + F.cross_entropy(r[:, 3:6], yu)
                             + F.binary_cross_entropy_with_logits(r[:, 6], yh)
                             + F.binary_cross_entropy_with_logits(r[:, 7], yl))

        lv = net.log_vars
        loss = sum(torch.exp(-lv[k]) * losses[t] + lv[k] for k, t in enumerate(("agri", "mineral", "thermal")))
        opt.zero_grad(set_to_none=True)
        loss.backward()
        torch.nn.utils.clip_grad_norm_(net.parameters(), 5.0)
        opt.step()
        sched.step()

        if step % args.eval_every == 0 or step == args.steps:
            m = evaluate(lambda t, x: torch_predict(net, t, x), {k: v for k, v in val.items() if k.endswith("_val")})
            score = selection_score(m)
            history.append({"step": step, "score": score, "loss": {k: float(v.detach()) for k, v in losses.items()},
                            "seconds": round(time.time() - t0)})
            print(f"step {step:6d}  score {score:.5f}  mineral {m['mineral_val']['class_agreement']:.4f}/"
                  f"{m['mineral_val']['abundance_mae']:.4f}  agri r2min {m['agri_val']['r2_min_core']:.4f}  "
                  f"thermal {m['thermal_val']['global_agreement']:.4f} {m['thermal_val']['local_agreement']:.4f}  "
                  f"[{time.time() - t0:.0f}s]", flush=True)
            if score > best:
                best = score
                best_state = {k: v.detach().clone() for k, v in net.state_dict().items()}
    net.load_state_dict(best_state)
    return net, val, history


# ── ONNX export and verification ────────────────────────────────────────────

def git_sha():
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], text=True).strip()
    except Exception:
        return "unknown"


def export_onnx(net, path, version, extra_meta):
    import onnx
    net.eval()
    model = HydraExport(net)
    dummy = (torch.zeros(4, len(spec.AGRI_BANDS_NM)), torch.zeros(4, spec.N_MINERAL_FEATURES),
             torch.zeros(4, len(spec.THERMAL_FEATURES)))
    dyn = {"x_agri": {0: "n_agri"}, "x_mineral": {0: "n_mineral"}, "x_thermal": {0: "n_thermal"}}
    for o in HydraExport.OUTPUTS:
        dyn[o] = {0: "n_" + ("agri" if o.startswith("agri") else o.split("_")[0])}
    torch.onnx.export(model, dummy, str(path), input_names=list(HydraExport.INPUTS),
                      output_names=list(HydraExport.OUTPUTS), dynamic_axes=dyn, opset_version=17,
                      dynamo=False)
    m = onnx.load(str(path))
    onnx.checker.check_model(m)
    meta = {"model_version": version, "feature_spec_hash": spec.feature_spec_hash(),
            "spec": json.dumps(spec.as_dict()), **{k: json.dumps(v) if not isinstance(v, str) else v
                                                  for k, v in extra_meta.items()}}
    for k, v in meta.items():
        p = m.metadata_props.add()
        p.key, p.value = k, v
    onnx.save(m, str(path))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="artifacts/data")
    ap.add_argument("--out", default="artifacts/models")
    ap.add_argument("--version", default="v1")
    ap.add_argument("--steps", type=int, default=30000)
    ap.add_argument("--batch", type=int, default=512)
    ap.add_argument("--lr", type=float, default=2e-3)
    ap.add_argument("--eval-every", type=int, default=1000)
    ap.add_argument("--threads", type=int, default=8)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--arch", choices=["mlp", "conv"], default="mlp")
    args = ap.parse_args(argv)

    out = Path(args.out) / args.version
    out.mkdir(parents=True, exist_ok=True)
    net, val, history = train(args)

    data_manifest = json.loads((Path(args.data) / "manifest.json").read_text())
    onnx_path = out / "hydra.onnx"
    export_onnx(net, onnx_path, args.version, {"git_sha": git_sha(), "data_manifest": data_manifest})

    from hydra.runtime import HydraRuntime
    rt = HydraRuntime(onnx_path)
    # 1) ONNX == PyTorch
    parity = {}
    for name in ("agri_val", "mineral_val", "thermal_val"):
        d, task = val[name], name.split("_")[0]
        a, b = torch_predict(net, task, d["x"][:4096]), rt.predict(task, np.nan_to_num(d["x"][:4096]))
        parity[task] = max(float(np.abs(a[k] - b[k]).max()) for k in a)
    # 2) every metric and gate, computed on the ONNX outputs
    metrics = evaluate(lambda t, x: rt.predict(t, np.nan_to_num(x)), val)
    gates = check_gates(metrics)
    passed = all(g["passed"] for g in gates.values() if g["passed"] is not None)

    card = {"model": "HydraNet", "architecture": args.arch, "version": args.version, "format": "onnx", "opset": 17,
            "file": "hydra.onnx", "bytes": onnx_path.stat().st_size,
            "parameters": int(sum(p.numel() for n, p in net.named_parameters() if n != "log_vars")),
            "feature_spec_hash": spec.feature_spec_hash(), "git_sha": git_sha(),
            "trained_to_reproduce": {
                "mineral": "SAM class (0.20 rad -> unknown) + SMA/FCLS abundance + quality (services/mineral_analysis)",
                "agriculture": f"PROSAIL LUT inversion, k={spec.LUT_K} inverse-distance (services/rtm_inversion approach)",
                "thermal": "ThermalAnomalyDetector global/local z, UHI p80/p20, hotspot p95 (services/thermal)"},
            "onnx_vs_torch_max_abs_diff": parity, "gates": gates, "all_gates_passed": passed,
            "train_args": vars(args), "data_manifest": data_manifest,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    (out / "metrics.json").write_text(json.dumps({"metrics": metrics, "history": history}, indent=2))
    (out / "model_card.json").write_text(json.dumps(card, indent=2))
    print(json.dumps({"gates": gates, "onnx_vs_torch": parity, "all_gates_passed": passed}, indent=2))
    return 0 if passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
