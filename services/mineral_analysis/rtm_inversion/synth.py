"""Synthetic training-set generator (library forward model).

Builds a balanced, fully-labelled training set for the inversion NN by SIMULATING
reflectance from the 12 USGS endmembers instead of relying on the scene (which
only contains a few of them).  Forward model = constrained linear mixing (the
same model SMA inverts), plus albedo and noise nuisances:

    r = (sum_i a_i * E_i) * brightness + noise,   a_i >= 0,  sum_i a_i = 1

The TRUE abundance vector `a` is the regression label; argmax(a) is the class.
Features come from the SAME features.FeatureExtractor used on the real image, so
the trained NN can be applied to real pixels without any feature drift.

Run:  python rtm_inversion/synth.py --n-samples 60000
Output: output/training_set/ (training_set.npz, schema.json, stats.json, sample csv)
"""
import json
import sys
from pathlib import Path

import numpy as np

_SERVICE_DIR = str(Path(__file__).resolve().parent.parent)
if _SERVICE_DIR not in sys.path:
    sys.path.insert(0, _SERVICE_DIR)

import config  # noqa: E402

try:
    from . import library_setup
    from .features import FeatureExtractor
except ImportError:
    import library_setup
    from features import FeatureExtractor

DEFAULT_N_SAMPLES = 60_000
# Probability of mixing k endmembers (index 0 -> k=1 pure, ... index 3 -> k=4).
MIX_K_PROBS = (0.30, 0.35, 0.25, 0.10)


def simulate(endmembers, n_samples, rng, k_probs=MIX_K_PROBS,
             brightness=(0.6, 1.2), noise=(0.003, 0.015), refl_clip=(0.001, 1.5)):
    """Return (spectra (n, n_bands), abundances (n, n_em)) from the mixing model."""
    n_em, n_bands = endmembers.shape
    ks = rng.choice(np.arange(1, len(k_probs) + 1), size=n_samples, p=np.asarray(k_probs))

    abundances = np.zeros((n_samples, n_em), dtype=np.float64)
    for i, k in enumerate(ks):
        members = rng.choice(n_em, size=int(k), replace=False)
        abundances[i, members] = rng.dirichlet(np.ones(int(k)))

    spectra = abundances @ endmembers                      # linear mixing
    spectra *= rng.uniform(*brightness, size=(n_samples, 1))   # albedo nuisance
    sigma = rng.uniform(*noise, size=(n_samples, 1))
    spectra += rng.normal(0.0, 1.0, spectra.shape) * sigma     # additive noise
    np.clip(spectra, *refl_clip, out=spectra)
    return spectra.astype(np.float32), abundances.astype(np.float32)


def generate(library_dir=None, n_samples=DEFAULT_N_SAMPLES, seed=0, pure_per_mineral=200):
    """Simulate spectra, extract features, return everything needed to train/eval."""
    library_dir = Path(library_dir or config.SPECTRAL_LIBRARY_DIR)
    rng = np.random.default_rng(seed)

    wl_full, good = library_setup.emit_band_table()
    wl_good = wl_full[np.flatnonzero(good)]
    names, endmembers = library_setup.load_aligned_library(library_dir, wl_good)
    extractor = FeatureExtractor(wl_good, endmembers, names)

    spectra, abund = simulate(endmembers, n_samples, rng)

    # Guarantee clean pure examples of every endmember (noise/albedo still applied).
    if pure_per_mineral > 0:
        n_em = len(names)
        pure_ab = np.zeros((n_em * pure_per_mineral, n_em), dtype=np.float32)
        for m in range(n_em):
            pure_ab[m * pure_per_mineral:(m + 1) * pure_per_mineral, m] = 1.0
        pure_spec = pure_ab @ endmembers
        pure_spec *= rng.uniform(0.6, 1.2, size=(pure_spec.shape[0], 1))
        pure_spec += rng.normal(0, 1, pure_spec.shape) * rng.uniform(0.003, 0.015, size=(pure_spec.shape[0], 1))
        np.clip(pure_spec, 0.001, 1.5, out=pure_spec)
        spectra = np.vstack([spectra, pure_spec.astype(np.float32)])
        abund = np.vstack([abund, pure_ab])

    order = rng.permutation(spectra.shape[0])
    spectra, abund = spectra[order], abund[order]

    X = extractor.transform(spectra)
    finite = np.isfinite(X).all(axis=1)
    X, abund, spectra = X[finite], abund[finite], spectra[finite]
    y_class = abund.argmax(axis=1).astype(np.int16)
    return X, abund, y_class, spectra, extractor.feature_names(), names, wl_good


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="Generate the synthetic 12-mineral training set")
    ap.add_argument("--library")
    ap.add_argument("--output")
    ap.add_argument("--n-samples", type=int, default=DEFAULT_N_SAMPLES)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args(argv)

    out_dir = Path(args.output or (config.OUTPUT_DIR / "training_set"))
    out_dir.mkdir(parents=True, exist_ok=True)

    X, y_abund, y_class, spectra, feat_names, names, wl_good = generate(
        library_dir=args.library, n_samples=args.n_samples, seed=args.seed)

    np.savez_compressed(
        out_dir / "training_set.npz",
        X=X.astype(np.float32), y_abund=y_abund.astype(np.float32),
        y_class=y_class.astype(np.int16), spectra=spectra.astype(np.float32),
        feature_names=np.array(feat_names, dtype=object),
        class_names=np.array(names, dtype=object),
        wavelengths_nm=wl_good.astype(np.float32),
    )

    uniq, cnt = np.unique(y_class, return_counts=True)
    stats = {
        "n_samples": int(X.shape[0]), "n_features": int(X.shape[1]), "n_minerals": len(names),
        "source": "synthetic linear-mixing forward model from USGS library",
        "dominant_class_distribution": {names[int(c)]: int(n) for c, n in zip(uniq, cnt)},
        "mean_active_endmembers": float((y_abund > 0.05).sum(1).mean()),
    }
    (out_dir / "stats.json").write_text(json.dumps(stats, indent=2), encoding="utf-8")
    schema = {
        "X": {"shape": list(X.shape), "desc": "features (same transform as the real image)"},
        "y_abund": {"shape": list(y_abund.shape), "desc": "TRUE abundance fractions (regression target)"},
        "y_class": {"shape": list(y_class.shape), "desc": "argmax(y_abund) dominant mineral index"},
        "spectra": {"desc": "simulated reflectance on wavelengths_nm (good bands)"},
        "class_names": list(names), "feature_names": list(feat_names),
        "wavelengths_nm": [float(w) for w in wl_good],
    }
    (out_dir / "schema.json").write_text(json.dumps(schema, indent=2), encoding="utf-8")

    import csv
    with open(out_dir / "training_sample.csv", "w", newline="", encoding="utf-8") as f:
        wr = csv.writer(f)
        wr.writerow(feat_names + [f"abund_{n}" for n in names] + ["dominant"])
        for i in range(min(2000, X.shape[0])):
            wr.writerow([f"{v:.6g}" for v in X[i]] + [f"{v:.6g}" for v in y_abund[i]] + [names[int(y_class[i])]])

    print(f"training set: X={X.shape}, {len(names)} minerals -> {out_dir}")
    print("dominant-class counts:", stats["dominant_class_distribution"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
