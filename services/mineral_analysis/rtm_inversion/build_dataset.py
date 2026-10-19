"""Assemble an ML-ready mineral training dataset from the EMIT cube.

Inputs
------
* the EMIT GeoTIFF cube (good bands selected via band_metadata.json), and
* the SAM/SMA/quality reference maps produced by the normal pipeline (main.py).

The SAM dominant-mineral class and the SMA abundance vector are the *labels*;
the per-pixel feature vector (features.FeatureExtractor) is the *input*.  Only
confidently classified, finite pixels are kept, optionally class-balanced and
capped, so the exported set is clean and learnable.

Outputs (in <output>/dataset/)
  dataset.npz        X, y_class, y_abund, quality, wavelengths, names, pixel rows/cols
  dataset_sample.csv readable sample for eyeballing
  schema.json        column-by-column description for the NN team
  stats.json         class distribution + sanity numbers
"""
import json
import sys
from pathlib import Path

import numpy as np

_SERVICE_DIR = str(Path(__file__).resolve().parent.parent)
if _SERVICE_DIR not in sys.path:
    sys.path.insert(0, _SERVICE_DIR)

import config  # noqa: E402  (import first: sets PROJ_DATA before rasterio loads)
import raster_io  # noqa: E402

try:  # works both as `python -m rtm_inversion.build_dataset` and `python build_dataset.py`
    from . import library_setup
    from .features import FeatureExtractor
except ImportError:  # run as a plain script: the package dir is on sys.path[0]
    import library_setup
    from features import FeatureExtractor

DEFAULT_MAX_SAMPLES = 60_000


def _read_reference_maps(normal_dir):
    """Load SAM class map, SMA abundance stack, quality map, and the id->name map."""
    normal_dir = Path(normal_dir)
    meta = json.loads((normal_dir / "processing_metadata.json").read_text(encoding="utf-8"))
    id_to_name = {int(k): v for k, v in meta["minerals"].items()}  # class-map id -> mineral

    cls = raster_io.read_raster(normal_dir / config.CLASSIFICATION_FILE)
    abund = raster_io.read_raster(normal_dir / config.ABUNDANCE_FILE)
    qual = raster_io.read_raster(normal_dir / config.QUALITY_FILE)
    class_ids = np.rint(np.nan_to_num(cls.data[0], nan=0.0)).astype(int)  # (h, w)
    return class_ids, abund.data, qual.data[0], id_to_name, meta


def build(cube_path=None, library_dir=None, normal_dir=None, output_dir=None,
          max_samples=DEFAULT_MAX_SAMPLES, balanced=True, seed=0):
    cube_path = Path(cube_path or config.INPUT_RASTER)
    library_dir = Path(library_dir or config.SPECTRAL_LIBRARY_DIR)
    normal_dir = Path(normal_dir or (config.OUTPUT_DIR / "normal_sam"))
    out_dir = Path(output_dir or (config.OUTPUT_DIR / "dataset"))
    out_dir.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(seed)

    # 1. Reflectance cube -> good bands only (same selection the normal run used).
    ref = raster_io.read_raster(cube_path, config.APPLY_SCALE_OFFSET)
    wl_full, good = library_setup.emit_band_table()
    if ref.count != wl_full.size:
        raise ValueError(f"cube has {ref.count} bands but band_metadata has {wl_full.size}")
    good_idx = np.flatnonzero(good)
    wl_good = wl_full[good_idx]
    h, w = ref.height, ref.width

    # 2. Endmembers + feature extractor on the identical grid.
    names, endmembers = library_setup.load_aligned_library(library_dir, wl_good)
    extractor = FeatureExtractor(wl_good, endmembers, names)

    # 3. Reference labels from the normal SAM/SMA run.
    class_ids, abund_stack, quality, id_to_name, normal_meta = _read_reference_maps(normal_dir)
    name_to_idx = {n.lower(): i for i, n in enumerate(names)}
    # Map class-map ids (1-based, 0 = unclassified) onto library endmember indices.
    id_label = np.full(class_ids.max() + 1, -1, dtype=int)
    for cid, nm in id_to_name.items():
        if nm.lower() in name_to_idx:
            id_label[cid] = name_to_idx[nm.lower()]

    flat_ids = class_ids.reshape(-1)
    labeled = flat_ids > 0
    y_class_all = np.where(labeled, id_label[np.clip(flat_ids, 0, id_label.size - 1)], -1)
    labeled &= y_class_all >= 0

    # 4. Choose sample pixels (optionally class-balanced), then extract features.
    idx_all = np.flatnonzero(labeled)
    if balanced:
        per_class = max(1, max_samples // max(1, len(names)))
        chosen = []
        for c in range(len(names)):
            members = idx_all[y_class_all[idx_all] == c]
            if members.size:
                take = min(per_class, members.size)
                chosen.append(rng.choice(members, size=take, replace=False))
        sample_idx = np.concatenate(chosen) if chosen else idx_all[:0]
    else:
        take = min(max_samples, idx_all.size)
        sample_idx = rng.choice(idx_all, size=take, replace=False)
    rng.shuffle(sample_idx)

    pixels = raster_io.to_pixels(ref.data[good_idx])  # (h*w, n_good)
    X = extractor.transform(pixels[sample_idx])
    feat_names = extractor.feature_names()
    y_class = y_class_all[sample_idx]
    abund_pix = raster_io.to_pixels(abund_stack)  # (h*w, n_minerals)
    y_abund = abund_pix[sample_idx]
    q = quality.reshape(-1)[sample_idx]
    rows, cols = np.divmod(sample_idx, w)

    # Drop any rows that came out non-finite in the features.
    finite = np.isfinite(X).all(axis=1)
    X, y_class, y_abund, q = X[finite], y_class[finite], y_abund[finite], q[finite]
    rows, cols, kept_idx = rows[finite], cols[finite], sample_idx[finite]

    _write_outputs(out_dir, X, y_class, y_abund, q, rows, cols, kept_idx,
                   feat_names, names, wl_good, normal_meta)
    return out_dir, X.shape, names
# <<APPEND_POINT>>


def _write_outputs(out_dir, X, y_class, y_abund, q, rows, cols, kept_idx,
                   feat_names, class_names, wl_good, normal_meta):
    out_dir = Path(out_dir)

    # 1. Compressed arrays: the canonical artefact for the NN team.
    np.savez_compressed(
        out_dir / "dataset.npz",
        X=X.astype(np.float32),
        y_class=y_class.astype(np.int16),
        y_abund=y_abund.astype(np.float32),
        quality=q.astype(np.float32),
        pixel_row=rows.astype(np.int32),
        pixel_col=cols.astype(np.int32),
        pixel_index=kept_idx.astype(np.int64),
        feature_names=np.array(feat_names, dtype=object),
        class_names=np.array(class_names, dtype=object),
        abundance_names=np.array(class_names, dtype=object),
        wavelengths_nm=wl_good.astype(np.float32),
    )

    # 2. Readable CSV sample (features + labels) for eyeballing.
    import csv
    n_sample = min(2000, X.shape[0])
    header = feat_names + ["label_index", "label_name"] + [f"abund_{n}" for n in class_names] + ["quality"]
    with open(out_dir / "dataset_sample.csv", "w", newline="", encoding="utf-8") as f:
        wr = csv.writer(f)
        wr.writerow(header)
        for i in range(n_sample):
            row = [f"{v:.6g}" for v in X[i]]
            row += [int(y_class[i]), class_names[int(y_class[i])]]
            row += [f"{v:.6g}" for v in y_abund[i]]
            row += [f"{q[i]:.6g}"]
            wr.writerow(row)

    # 3. Schema for the hand-off.
    n_cr = wl_good.size
    schema = {
        "n_samples": int(X.shape[0]),
        "n_features": int(X.shape[1]),
        "feature_groups": {
            "continuum_removed_reflectance": {"columns": [0, n_cr], "count": int(n_cr),
                                              "desc": "continuum-removed reflectance per good band"},
            "absorption_depths": {"desc": "1 - min(continuum-removed) in each diagnostic window"},
            "sam_angles": {"desc": "SAM spectral angle (rad) to each endmember, on CR spectra"},
            "brightness": {"desc": "mean reflectance across good bands"},
        },
        "targets": {
            "y_class": "SAM dominant-mineral index into class_names",
            "y_abund": "SMA abundance fraction per mineral (same order as class_names)",
            "quality": "heuristic SAM/SMA confidence in [0,1]",
        },
        "class_names": list(class_names),
        "wavelengths_nm": [float(w) for w in wl_good],
        "feature_names": list(feat_names),
        "provenance": {
            "input_raster": normal_meta.get("input_raster"),
            "bands_used": normal_meta.get("bands"),
            "sam_sma_settings": normal_meta.get("settings"),
            "note": "labels are SAM/SMA pseudo-labels from the normal pipeline, not field truth",
        },
    }
    (out_dir / "schema.json").write_text(json.dumps(schema, indent=2, default=str), encoding="utf-8")

    # 4. Class distribution + sanity numbers.
    uniq, counts = np.unique(y_class, return_counts=True)
    stats = {
        "n_samples": int(X.shape[0]),
        "class_distribution": {class_names[int(c)]: int(n) for c, n in zip(uniq, counts)},
        "feature_nan_fraction": float(np.mean(~np.isfinite(X))),
        "quality_mean": float(np.nanmean(q)) if q.size else None,
        "abundance_sum_mean": float(np.nanmean(y_abund.sum(axis=1))) if y_abund.size else None,
    }
    (out_dir / "stats.json").write_text(json.dumps(stats, indent=2), encoding="utf-8")


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="Build the mineral training dataset from the EMIT cube + SAM/SMA maps")
    ap.add_argument("--cube", help="EMIT GeoTIFF cube (default: config.INPUT_RASTER)")
    ap.add_argument("--library", help="USGS library dir (default: config.SPECTRAL_LIBRARY_DIR)")
    ap.add_argument("--normal-dir", help="dir with the normal SAM/SMA maps (default: output/normal_sam)")
    ap.add_argument("--output", help="dataset output dir (default: output/dataset)")
    ap.add_argument("--max-samples", type=int, default=DEFAULT_MAX_SAMPLES)
    ap.add_argument("--no-balanced", action="store_true", help="random sampling instead of class-balanced")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args(argv)

    out_dir, shape, names = build(
        cube_path=args.cube, library_dir=args.library, normal_dir=args.normal_dir,
        output_dir=args.output, max_samples=args.max_samples,
        balanced=not args.no_balanced, seed=args.seed,
    )
    print(f"dataset written to {out_dir}: X={shape}, {len(names)} classes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
