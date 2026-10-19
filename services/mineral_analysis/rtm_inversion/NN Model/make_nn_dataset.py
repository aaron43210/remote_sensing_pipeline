"""Convert output/training_set/training_set.npz (features + true abundances, built
by rtm_inversion/synth.py) into the SpectralDataset .npz format nn_main.py trains on.

The SpectralTransformer takes continuum-removed reflectance as input, which is
exactly the first `len(wavelengths)` feature columns (the `cr_*` block) of X; the
same continuum-removed representation nn_main produces at inference time. True
synthetic abundances become the regression target and argmax the class target.

Run:  python make_nn_dataset.py        # -> output/training_set/nn_dataset.npz
"""
import argparse
from pathlib import Path

import numpy as np

_SERVICE_DIR = Path(__file__).resolve().parents[2]
_DEFAULT_IN = _SERVICE_DIR / "output" / "training_set" / "training_set.npz"
_DEFAULT_OUT = _SERVICE_DIR / "output" / "training_set" / "nn_dataset.npz"


def convert(in_path=_DEFAULT_IN, out_path=_DEFAULT_OUT):
    d = np.load(in_path, allow_pickle=True)
    X = d["X"]
    wl = np.asarray(d["wavelengths_nm"], dtype=np.float64)
    names = [str(x) for x in d["class_names"]]
    feat = [str(x) for x in d["feature_names"]]
    n_wl = wl.size

    # First n_wl features are the continuum-removed reflectance bands.
    if not all(f.startswith("cr_") for f in feat[:n_wl]):
        raise ValueError("first feature columns are not the 'cr_' continuum-removed block")
    spectra = np.ascontiguousarray(X[:, :n_wl], dtype=np.float32)

    y_class = np.asarray(d["y_class"]).astype(np.int64)
    y_abund = np.asarray(d["y_abund"], dtype=np.float32)
    n = spectra.shape[0]

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    np.savez(
        out_path,
        spectra=spectra,
        wavelengths=wl,
        class_targets=y_class,
        abundances=y_abund,
        group_ids=np.arange(n, dtype=np.int64),
        sample_ids=np.arange(n, dtype=np.int64),
        mineral_names=np.asarray(names),  # unicode: loads with allow_pickle=False
    )
    print(f"wrote {out_path}  ({n} samples, {n_wl} bands, {len(names)} minerals)")
    return out_path


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="training_set.npz -> SpectralDataset nn_dataset.npz")
    ap.add_argument("--input", default=str(_DEFAULT_IN))
    ap.add_argument("--output", default=str(_DEFAULT_OUT))
    convert(ap.parse_args().input, ap.parse_args().output)
