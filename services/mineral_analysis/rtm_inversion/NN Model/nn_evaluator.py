"""Evaluates a trained Spectral Transformer on unseen test data.

The report keeps two things apart:
  teacher_agreement    - how closely the NN reproduces SAM/SMA. This measures imitation
                         fidelity only; it says nothing about scientific accuracy.
  independent_accuracy - NN vs independent ground truth, present only if supplied.
Metrics are plain NumPy; only predict() needs PyTorch. The model is never modified.
Run:  python nn_evaluator.py
"""
import json
from pathlib import Path

import numpy as np

import nn_config

REPORT_FILENAME = "evaluation_report.json"
REQUIRED_KEYS = ("spectrum", "sam_class_index", "sma_abundance", "mineral_names", "wavelengths")


def _labels(values, n_classes, name):
    a = np.asarray(values)
    if a.ndim != 1 or not np.issubdtype(a.dtype, np.integer):
        raise ValueError(f"{name} must be a 1-D integer array")
    if a.size and (a.min() < 0 or a.max() >= n_classes):
        raise ValueError(f"{name} has class ids outside 0..{n_classes - 1}")
    return a.astype(np.int64)


def classification_metrics(y_true, y_pred, class_names):
    """Accuracy, precision/recall/F1 (per class, macro, weighted) and confusion matrix.

    Confusion matrix rows = reference class, columns = NN class. Undefined ratios
    (no predictions or no support for a class) count as 0. Macro averages cover
    classes that appear in either the reference or the NN output.
    """
    k = len(class_names)
    yt, yp = _labels(y_true, k, "y_true"), _labels(y_pred, k, "y_pred")
    if yt.shape != yp.shape or yt.size == 0:
        raise ValueError("y_true and y_pred must be non-empty and the same length")

    cm = np.bincount(yt * k + yp, minlength=k * k).reshape(k, k)
    tp = np.diag(cm).astype(float)
    support, predicted = cm.sum(axis=1).astype(float), cm.sum(axis=0).astype(float)
    precision = np.divide(tp, predicted, out=np.zeros(k), where=predicted > 0)
    recall = np.divide(tp, support, out=np.zeros(k), where=support > 0)
    denom = precision + recall
    f1 = np.divide(2 * precision * recall, denom, out=np.zeros(k), where=denom > 0)
    present = (support + predicted) > 0
    weighted = lambda v: float((v * support).sum() / support.sum())

    return {
        "accuracy": float(tp.sum() / cm.sum()),
        "macro_precision": float(precision[present].mean()),
        "macro_recall": float(recall[present].mean()),
        "macro_f1": float(f1[present].mean()),
        "weighted_precision": weighted(precision),
        "weighted_recall": weighted(recall),
        "weighted_f1": weighted(f1),
        "per_class": {
            name: {"precision": float(precision[i]), "recall": float(recall[i]),
                   "f1": float(f1[i]), "support": int(support[i])}
            for i, name in enumerate(class_names)
        },
        "confusion_matrix": cm.tolist(),
        "class_names": list(class_names),
    }


def abundance_metrics(pred, target, class_names):
    """MAE, RMSE, signed mean error (NN - reference), max abs error; overall and per mineral."""
    p, t = np.asarray(pred, dtype=float), np.asarray(target, dtype=float)
    if p.ndim != 2 or p.shape != t.shape or p.shape[0] == 0 or p.shape[1] != len(class_names):
        raise ValueError(f"abundances must be non-empty, shape (n, {len(class_names)}); got {p.shape} and {t.shape}")
    err = p - t
    abs_err = np.abs(err)
    return {
        "mae": float(abs_err.mean()),
        "rmse": float(np.sqrt((err ** 2).mean())),
        "mean_error": float(err.mean()),
        "max_abs_error": float(abs_err.max()),
        "per_mineral": {
            name: {"mae": float(abs_err[:, i].mean()),
                   "rmse": float(np.sqrt((err[:, i] ** 2).mean())),
                   "mean_error": float(err[:, i].mean()),
                   "max_abs_error": float(abs_err[:, i].max())}
            for i, name in enumerate(class_names)
        },
    }


def similarity_metrics(pred_probs, teacher_angles, temperature):
    """Difference between NN class probabilities and the teacher's angle-derived soft target.

    The NN has no angle output, so the teacher side is softmax(-angle / temperature),
    the same target used for angle distillation. Values depend on `temperature`.
    """
    if temperature <= 0:
        raise ValueError("temperature must be positive")
    p, a = np.asarray(pred_probs, dtype=float), np.asarray(teacher_angles, dtype=float)
    if p.shape != a.shape or p.ndim != 2:
        raise ValueError("pred_probs and teacher_angles must have the same (n, n_classes) shape")
    ok = np.isfinite(a).all(axis=1)
    if not ok.any():
        raise ValueError("no samples with finite teacher angles")
    z = -a[ok] / temperature
    z -= z.max(axis=1, keepdims=True)
    soft = np.exp(z)
    soft /= soft.sum(axis=1, keepdims=True)
    diff = np.abs(p[ok] - soft)
    return {"mean_abs_diff": float(diff.mean()), "max_abs_diff": float(diff.max()),
            "n_samples": int(ok.sum()), "temperature": float(temperature)}


def _valid_mask(n, ref_class, ref_abundance):
    valid = np.ones(n, dtype=bool)
    if ref_class is not None:
        rc = np.asarray(ref_class)
        if rc.shape != (n,):
            raise ValueError("reference class array must have one entry per sample")
        valid &= rc >= 0  # drops teacher "unknown"/"invalid" codes
    if ref_abundance is not None:
        ra = np.asarray(ref_abundance, dtype=float)
        if ra.ndim != 2 or ra.shape[0] != n:
            raise ValueError("reference abundance must have shape (n_samples, n_classes)")
        valid &= np.isfinite(ra).all(axis=1)
    return valid


def compare(pred_class, pred_abundance, ref_class, ref_abundance, class_names):
    """Compare NN output with one reference (teacher or independent truth)."""
    pc = np.asarray(pred_class)
    pa = np.asarray(pred_abundance, dtype=float)
    n = pc.shape[0]
    if pa.shape != (n, len(class_names)):
        raise ValueError(f"NN abundance must have shape ({n}, {len(class_names)}), got {pa.shape}")
    if not np.isfinite(pa).all():
        raise ValueError("NN predictions contain NaN/inf")

    valid = _valid_mask(n, ref_class, ref_abundance)
    if not valid.any():
        raise ValueError("no valid reference samples to compare against")
    out = {"n_samples": int(valid.sum()), "n_excluded": int((~valid).sum())}
    if ref_class is not None:
        out["classification"] = classification_metrics(np.asarray(ref_class)[valid], pc[valid], class_names)
    if ref_abundance is not None:
        out["abundance"] = abundance_metrics(pa[valid], np.asarray(ref_abundance)[valid], class_names)
    return out


def evaluate_nn(pred, teacher, class_names, independent=None, temperature=None):
    """Build the evaluation report.

    pred:        {"class_index", "class_probs", "abundance"} from the NN
    teacher:     {"class_index", "abundance", "angles" (optional)} from SAM/SMA
    independent: {"class_index" and/or "abundance", "source": str} or None
    """
    if temperature is None:
        temperature = getattr(nn_config, "ANGLE_DISTILLATION_TEMPERATURE", 0.1)

    report = {
        "class_names": list(class_names),
        "teacher_agreement": compare(pred["class_index"], pred["abundance"], teacher["class_index"],
                                     teacher["abundance"], class_names),
        "similarity_agreement": None,
        "independent_accuracy": None,
        "notes": [
            "teacher_agreement measures how closely the NN imitates SAM/SMA, not scientific accuracy.",
            "The NN can only be as accurate as the teacher it imitates unless independent_accuracy says otherwise.",
        ],
    }

    if teacher.get("angles") is not None:
        valid = _valid_mask(len(pred["class_index"]), teacher["class_index"], teacher["abundance"])
        report["similarity_agreement"] = similarity_metrics(
            np.asarray(pred["class_probs"])[valid], np.asarray(teacher["angles"])[valid], temperature)

    if independent is not None:
        block = compare(pred["class_index"], pred["abundance"], independent.get("class_index"),
                        independent.get("abundance"), class_names)
        block["source"] = independent.get("source", "unspecified")
        report["independent_accuracy"] = block
    else:
        report["notes"].append("No independent ground truth supplied: accuracy beyond the teacher is unassessed.")
    return report


def predict(checkpoint_path, spectra, batch_size=None, device=None):
    """Run the saved NN on spectra (n, n_wavelengths); inference only, no USGS/SAM/SMA."""
    import torch
    from spectral_transformer import SpectralTransformer

    checkpoint = torch.load(checkpoint_path, map_location="cpu", weights_only=True)
    config = checkpoint["model_config"]
    x = np.asarray(spectra, dtype=np.float32)
    if x.ndim != 2 or x.shape[0] == 0 or x.shape[1] != config["num_wavelengths"]:
        raise ValueError(f"spectra must have shape (n, {config['num_wavelengths']}), got {x.shape}")
    if not np.isfinite(x).all():
        raise ValueError("test spectra contain NaN/inf; remove invalid samples first")

    device = torch.device(device or ("cuda" if torch.cuda.is_available() else "cpu"))
    model = SpectralTransformer(**config)
    model.load_state_dict(checkpoint["model_state"])
    model.to(device).eval()

    batch_size = batch_size or nn_config.BATCH_SIZE
    probs, abundance = [], []
    with torch.no_grad():
        for start in range(0, len(x), batch_size):
            logits, ab = model(torch.from_numpy(x[start:start + batch_size]).to(device))
            probs.append(torch.softmax(logits, dim=1).cpu().numpy())
            abundance.append(ab.cpu().numpy())
    probs = np.concatenate(probs)
    return {"class_index": probs.argmax(axis=1), "class_probs": probs,
            "abundance": np.concatenate(abundance),
            "mineral_names": list(checkpoint["mineral_names"]),
            "wavelengths": checkpoint["wavelengths"].numpy()}


def load_test_arrays(path, truth_keys=None, truth_source="unspecified"):
    """Read a test .npz holding the spectra and their stored teacher targets.

    truth_keys: optional (class_key, abundance_key) naming arrays in the file that hold
    independent ground truth; either may be absent from the file.
    """
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"test data not found: {path}")
    with np.load(path, allow_pickle=True) as f:
        missing = [k for k in REQUIRED_KEYS if k not in f.files]
        if missing:
            raise ValueError(f"{path} is missing arrays: {missing}")
        data = {
            "spectrum": f["spectrum"], "wavelengths": f["wavelengths"],
            "mineral_names": list(f["mineral_names"].tolist()),
            "teacher": {"class_index": f["sam_class_index"], "abundance": f["sma_abundance"],
                        "angles": f["sam_angles"] if "sam_angles" in f.files else None},
            "independent": None,
        }
        if truth_keys:
            cls_key, ab_key = truth_keys
            truth = {"class_index": f[cls_key] if cls_key in f.files else None,
                     "abundance": f[ab_key] if ab_key in f.files else None}
            if any(v is not None for v in truth.values()):
                data["independent"] = {**truth, "source": truth_source}
    return data


def evaluate_checkpoint(checkpoint_path, test_path, truth_keys=None, truth_source="unspecified"):
    """Load the model and test set, predict, and return the evaluation report."""
    data = load_test_arrays(test_path, truth_keys, truth_source)
    pred = predict(checkpoint_path, data["spectrum"])
    if pred["mineral_names"] != data["mineral_names"]:
        raise ValueError("model and test data use different minerals (or order)")
    wl_model, wl_data = np.asarray(pred["wavelengths"]), np.asarray(data["wavelengths"])
    if wl_model.shape != wl_data.shape or not np.allclose(wl_model, wl_data):
        raise ValueError("model and test data use different wavelength grids")
    return evaluate_nn(pred, data["teacher"], data["mineral_names"], data["independent"])


def evaluate_model(model, dataset, config=None):
    """nn_main glue: evaluate a loaded SpectralTransformer against a SpectralDataset.

    Compares the NN against the dataset's (teacher) class_targets and abundances
    and returns a flat metrics dict (plus nested classification/abundance blocks).
    """
    import torch

    model.eval()
    device = next(model.parameters()).device
    spectra = np.asarray(dataset.spectra, dtype=np.float32)
    y_true = np.asarray(dataset.class_targets).astype(np.int64)
    ab_true = np.asarray(dataset.abundances, dtype=float)
    names = list(dataset.mineral_names)
    bs = int(getattr(config, "batch_size", 256) or 256)

    preds, abus = [], []
    with torch.no_grad():
        for s in range(0, len(spectra), bs):
            out = model(torch.as_tensor(spectra[s:s + bs], dtype=torch.float32, device=device))
            preds.append(out.class_logits.argmax(1).cpu().numpy())
            abus.append(out.abundances.cpu().numpy())
    y_pred = np.concatenate(preds).astype(np.int64)
    ab_pred = np.concatenate(abus).astype(float)

    cls = classification_metrics(y_true, y_pred, names)
    ab = abundance_metrics(ab_pred, ab_true, names)
    return {
        "n_samples": int(len(spectra)),
        "accuracy": cls["accuracy"], "macro_f1": cls["macro_f1"], "weighted_f1": cls["weighted_f1"],
        "abundance_mae": ab["mae"], "abundance_rmse": ab["rmse"],
        "classification": cls, "abundance": ab,
    }


def format_report(report):
    def block_lines(title, block):
        lines = [f"== {title} ({block['n_samples']} samples, {block['n_excluded']} excluded) =="]
        c = block.get("classification")
        if c:
            lines.append(f"classification: accuracy {c['accuracy']:.4f} | macro P {c['macro_precision']:.4f} "
                         f"R {c['macro_recall']:.4f} F1 {c['macro_f1']:.4f} | weighted F1 {c['weighted_f1']:.4f}")
            for name, m in c["per_class"].items():
                lines.append(f"  {name:<14} P {m['precision']:.3f}  R {m['recall']:.3f}  F1 {m['f1']:.3f}  n={m['support']}")
            lines.append("  confusion matrix (rows=reference, cols=NN):")
            lines += ["    " + " ".join(f"{v:6d}" for v in row) for row in c["confusion_matrix"]]
        a = block.get("abundance")
        if a:
            lines.append(f"abundance: MAE {a['mae']:.4f} | RMSE {a['rmse']:.4f} | mean error {a['mean_error']:+.4f} "
                         f"| max abs error {a['max_abs_error']:.4f}")
            for name, m in a["per_mineral"].items():
                lines.append(f"  {name:<14} MAE {m['mae']:.4f}  RMSE {m['rmse']:.4f}  max {m['max_abs_error']:.4f}")
        return lines

    lines = block_lines("TEACHER AGREEMENT: NN vs SAM/SMA (imitation, not scientific accuracy)",
                        report["teacher_agreement"])
    s = report["similarity_agreement"]
    if s:
        lines.append(f"similarity (NN probs vs angle-derived target, T={s['temperature']}): "
                     f"mean abs diff {s['mean_abs_diff']:.4f}, max {s['max_abs_diff']:.4f}")
    ind = report["independent_accuracy"]
    if ind:
        lines += [""] + block_lines(f"INDEPENDENT ACCURACY vs {ind['source']}", ind)
    lines += [""] + [f"note: {n}" for n in report["notes"]]
    return "\n".join(lines)


def main():
    test_path = Path(nn_config.TEST_DATA_PATH)
    for other in (nn_config.TRAIN_DATA_PATH, nn_config.VAL_DATA_PATH):
        if test_path.resolve() == Path(other).resolve():
            raise ValueError("test data path equals the training/validation path; evaluate on unseen data")

    report = evaluate_checkpoint(
        nn_config.MODEL_DIR / nn_config.MODEL_FILENAME, test_path,
        truth_keys=("dominant_class", "abundance"),
        truth_source="synthetic mixing truth from training_data_generator (not real-world validation)")
    print(format_report(report))

    out = Path(nn_config.LOG_DIR) / REPORT_FILENAME
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2))
    print(f"\nreport saved to {out}")


if __name__ == "__main__":
    main()
