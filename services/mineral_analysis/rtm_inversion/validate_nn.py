"""Self-validation: train a small neural network on the exported dataset and
test it against the SAM/SMA reference, so we can be confident the dataset is
learnable *before* handing it to the NN designer team.

This NN is a throwaway sanity check, not the production model.  High held-out
agreement with the SAM/SMA labels means the features carry the mineral signal.
"""
import json
import sys
from pathlib import Path

import numpy as np

_SERVICE_DIR = str(Path(__file__).resolve().parent.parent)
if _SERVICE_DIR not in sys.path:
    sys.path.insert(0, _SERVICE_DIR)

import config  # noqa: E402

PASS_ACCURACY = 0.85  # held-out class-agreement needed to call the dataset "validated"


def _load(dataset_dir, filename="dataset.npz"):
    d = np.load(Path(dataset_dir) / filename, allow_pickle=True)
    return d


def validate(dataset_dir=None, output_dir=None, seed=0, npz="dataset.npz"):
    from sklearn.model_selection import train_test_split
    from sklearn.preprocessing import StandardScaler
    from sklearn.neural_network import MLPClassifier, MLPRegressor
    from sklearn.metrics import accuracy_score, f1_score, confusion_matrix, r2_score
    import joblib

    dataset_dir = Path(dataset_dir or (config.OUTPUT_DIR / "dataset"))
    out_dir = Path(output_dir or (config.OUTPUT_DIR / "validation"))
    out_dir.mkdir(parents=True, exist_ok=True)

    d = _load(dataset_dir, npz)
    X, y_class, y_abund = d["X"], d["y_class"].astype(int), d["y_abund"]
    class_names = [str(n) for n in d["class_names"]]

    strat = y_class if np.min(np.bincount(y_class)) >= 2 else None
    Xtr, Xte, ytr, yte, atr, ate = train_test_split(
        X, y_class, y_abund, test_size=0.25, random_state=seed, stratify=strat)

    scaler = StandardScaler().fit(Xtr)
    Xtr_s, Xte_s = scaler.transform(Xtr), scaler.transform(Xte)

    # --- Classifier: reproduce the SAM dominant-mineral label ---
    clf = MLPClassifier(hidden_layer_sizes=(128, 64), max_iter=300,
                        early_stopping=True, random_state=seed)
    clf.fit(Xtr_s, ytr)
    pred = clf.predict(Xte_s)
    acc = float(accuracy_score(yte, pred))
    f1 = float(f1_score(yte, pred, average="macro"))
    present = sorted(set(yte.tolist()) | set(pred.tolist()))
    cm = confusion_matrix(yte, pred, labels=present).tolist()

    # --- Regressor: reproduce the SMA abundance vector ---
    reg = MLPRegressor(hidden_layer_sizes=(128, 64), max_iter=300,
                       early_stopping=True, random_state=seed)
    reg.fit(Xtr_s, atr)
    apred = reg.predict(Xte_s)
    finite = np.isfinite(ate).all(axis=1)
    r2 = float(r2_score(ate[finite], apred[finite])) if finite.any() else float("nan")
    rmse = float(np.sqrt(np.mean((ate[finite] - apred[finite]) ** 2))) if finite.any() else float("nan")

    joblib.dump({"scaler": scaler, "classifier": clf, "regressor": reg,
                 "class_names": class_names}, out_dir / "validation_nn.joblib")

    passed = acc >= PASS_ACCURACY
    report = {
        "n_train": int(Xtr.shape[0]), "n_test": int(Xte.shape[0]),
        "class_accuracy": acc, "class_macro_f1": f1,
        "abundance_r2": r2, "abundance_rmse": rmse,
        "pass_threshold": PASS_ACCURACY, "validated": bool(passed),
        "classes_present": [class_names[i] for i in present],
        "confusion_matrix": cm,
    }
    (out_dir / "validation_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    _write_report_md(out_dir / "validation_report.md", report, class_names)
    return report, out_dir
# <<APPEND>>


def _write_report_md(path, r, class_names):
    verdict = "PASS — dataset is learnable and consistent with SAM/SMA" if r["validated"] \
        else "REVIEW — held-out agreement is below threshold; inspect features/labels"
    lines = [
        "# Mineral dataset self-validation",
        "",
        f"**Verdict: {verdict}**",
        "",
        f"- Train / test samples: {r['n_train']} / {r['n_test']}",
        f"- Class agreement vs SAM (held-out): {r['class_accuracy']:.3f} "
        f"(threshold {r['pass_threshold']:.2f})",
        f"- Class macro-F1: {r['class_macro_f1']:.3f}",
        f"- Abundance R² vs SMA: {r['abundance_r2']:.3f}",
        f"- Abundance RMSE vs SMA: {r['abundance_rmse']:.4f}",
        f"- Classes present in test: {', '.join(r['classes_present'])}",
        "",
        "The labels are SAM/SMA pseudo-labels, so a high score means the exported",
        "features reproduce the normal method — the dataset is ready to hand off.",
    ]
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")


def full_image_map(dataset_dir=None, output_dir=None, cube_path=None,
                   library_dir=None, normal_dir=None):
    """Predict a mineral class map for the whole image with the validation NN and
    report its spatial agreement with the normal SAM classification."""
    import joblib
    import raster_io
    try:
        from . import library_setup
        from .features import FeatureExtractor
    except ImportError:
        import library_setup
        from features import FeatureExtractor

    out_dir = Path(output_dir or (config.OUTPUT_DIR / "validation"))
    bundle = joblib.load(out_dir / "validation_nn.joblib")
    scaler, clf, class_names = bundle["scaler"], bundle["classifier"], bundle["class_names"]

    ref = raster_io.read_raster(cube_path or config.INPUT_RASTER, config.APPLY_SCALE_OFFSET)
    wl_full, good = library_setup.emit_band_table()
    good_idx = np.flatnonzero(good)
    wl_good = wl_full[good_idx]
    names, endmembers = library_setup.load_aligned_library(
        library_dir or config.SPECTRAL_LIBRARY_DIR, wl_good)
    extractor = FeatureExtractor(wl_good, endmembers, names)

    pixels = raster_io.to_pixels(ref.data[good_idx])
    n = pixels.shape[0]
    pred = np.full(n, -1, dtype=np.int32)
    step = 50_000
    for s in range(0, n, step):
        sl = slice(s, s + step)
        feats = extractor.transform(pixels[sl])
        ok = np.isfinite(feats).all(axis=1)
        if ok.any():
            pred[sl][ok] = clf.predict(scaler.transform(feats[ok]))

    class_ids = np.where(pred >= 0, pred + 1, config.CLASS_NODATA_ID).astype(np.int32)
    class_name_map = {i + 1: n for i, n in enumerate(class_names)}
    nn_map_path = out_dir / "nn_classification.tif"
    raster_io.write_classification(
        nn_map_path, ref, raster_io.to_map(class_ids, ref.height, ref.width),
        class_name_map, config.CLASS_NODATA_ID)

    agreement = None
    normal_dir = Path(normal_dir or (config.OUTPUT_DIR / "normal_sam"))
    sam_map = normal_dir / config.CLASSIFICATION_FILE
    if sam_map.is_file():
        sam = raster_io.read_raster(sam_map)
        sam_ids = np.rint(np.nan_to_num(sam.data[0], nan=0.0)).astype(int).reshape(-1)
        both = (sam_ids > 0) & (class_ids > 0)
        if both.any():
            agreement = float(np.mean(sam_ids[both] == class_ids[both]))
    (out_dir / "full_map_agreement.json").write_text(
        json.dumps({"spatial_agreement_vs_sam": agreement,
                    "nn_classification": str(nn_map_path)}, indent=2), encoding="utf-8")
    return nn_map_path, agreement


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="Self-validate the mineral dataset with a small NN")
    ap.add_argument("--dataset", help="dataset dir (default: output/dataset)")
    ap.add_argument("--npz", default="dataset.npz", help="npz filename inside the dataset dir")
    ap.add_argument("--output", help="validation output dir (default: output/validation)")
    ap.add_argument("--full-map", action="store_true", help="also predict a whole-image NN map and compare to SAM")
    ap.add_argument("--cube"); ap.add_argument("--library"); ap.add_argument("--normal-dir")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args(argv)

    report, out_dir = validate(dataset_dir=args.dataset, output_dir=args.output, seed=args.seed, npz=args.npz)
    print(f"class agreement vs SAM: {report['class_accuracy']:.3f}  "
          f"abundance R²: {report['abundance_r2']:.3f}  validated={report['validated']}")
    if args.full_map:
        nn_map, agree = full_image_map(dataset_dir=args.dataset, output_dir=args.output,
                                       cube_path=args.cube, library_dir=args.library,
                                       normal_dir=args.normal_dir)
        print(f"wrote {nn_map}  spatial agreement vs SAM: "
              + (f"{agree:.3f}" if agree is not None else "n/a"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
