"""Final mineral prediction with the trained Spectral Transformer only.

No USGS library, SAM, SMA or teacher code is imported or called here. Input spectra
must already be preprocessed, wavelength-aligned and continuum-removed, on the same
wavelength grid the model was trained on; none of that is done in this file.
"""
from pathlib import Path

import numpy as np
import torch

import nn_config
from spectral_transformer import SpectralTransformer

INVALID = -1
REQUIRED_KEYS = ("model_state", "model_config", "mineral_names", "wavelengths")


class NNPredictor:
    """Loads a checkpoint written by train_nn.py and predicts from spectra.

    The model is rebuilt from the settings stored in the checkpoint, and mineral names
    and wavelengths also come from it, so later edits to nn_config.py cannot change them.
    """

    def __init__(self, checkpoint_path=None, device=None, batch_size=None):
        path = Path(checkpoint_path or nn_config.MODEL_DIR / nn_config.MODEL_FILENAME)
        if not path.is_file():
            raise FileNotFoundError(f"model checkpoint not found: {path}")
        checkpoint = torch.load(path, map_location="cpu", weights_only=True)
        missing = [k for k in REQUIRED_KEYS if k not in checkpoint]
        if missing:
            raise ValueError(f"{path} is not a valid checkpoint; missing: {missing}")

        config = checkpoint["model_config"]
        self.mineral_names = list(checkpoint["mineral_names"])
        self.wavelengths = np.asarray(checkpoint["wavelengths"].numpy(), dtype=np.float64)
        if (config["num_classes"] != len(self.mineral_names)
                or config["num_wavelengths"] != self.wavelengths.size):
            raise ValueError("checkpoint is inconsistent: model_config disagrees with its minerals/wavelengths")

        self.batch_size = nn_config.BATCH_SIZE if batch_size is None else batch_size
        if self.batch_size < 1:
            raise ValueError("batch_size must be >= 1")
        self.device = torch.device(device or ("cuda" if torch.cuda.is_available() else "cpu"))
        self.model = SpectralTransformer(**config)
        self.model.load_state_dict(checkpoint["model_state"])
        self.model.to(self.device).eval()

    def _forward(self, x):
        with torch.inference_mode():
            logits, abundance = self.model(torch.from_numpy(x).to(self.device))
            probs = torch.softmax(logits, dim=1)
        return probs.cpu().numpy(), abundance.cpu().numpy()

    def predict(self, spectra, wavelengths=None, top_k=None, batch_size=None):
        """Predict for one spectrum (n_wavelengths,) or many (n_samples, n_wavelengths).

        wavelengths: optional wavelengths of the input columns; if given they must match the
        training grid. Without it only the band count can be checked.
        Rows with NaN/inf or all zeros are marked invalid (class -1, "invalid", NaN scores)
        and never reach the model. Large inputs are processed in batches, and may be
        memory-mapped; the results (n_samples x n_minerals arrays) are held in memory.
        confidence is the highest softmax score: a model score, not a calibrated probability.
        """
        s = spectra if isinstance(spectra, np.ndarray) else np.asarray(spectra)
        if s.ndim == 1:
            s = s[None, :]
        n_bands, k = self.wavelengths.size, len(self.mineral_names)
        if s.ndim != 2 or s.shape[0] == 0 or s.shape[1] != n_bands:
            raise ValueError(f"spectra must be non-empty with shape (n, {n_bands}), got {s.shape}")
        if wavelengths is not None:
            w = np.asarray(wavelengths, dtype=float)
            if w.shape != self.wavelengths.shape or not np.allclose(w, self.wavelengths, rtol=1e-6, atol=1e-6):
                raise ValueError("input wavelengths differ from the model's training wavelength grid")
        if top_k is not None and not 1 <= top_k <= k:
            raise ValueError(f"top_k must be between 1 and {k}")
        bs = self.batch_size if batch_size is None else batch_size
        if bs < 1:
            raise ValueError("batch_size must be >= 1")

        n = s.shape[0]
        class_index = np.full(n, INVALID, dtype=np.int32)
        probs = np.full((n, k), np.nan, dtype=np.float32)
        abundance = np.full((n, k), np.nan, dtype=np.float32)
        valid = np.zeros(n, dtype=bool)

        for start in range(0, n, bs):
            sl = slice(start, start + bs)
            chunk = np.asarray(s[sl], dtype=np.float32)
            ok = np.isfinite(chunk).all(axis=1) & (chunk != 0).any(axis=1)
            valid[sl] = ok
            if not ok.any():
                continue
            p, a = self._forward(np.ascontiguousarray(chunk[ok]))
            if p.shape != (int(ok.sum()), k) or a.shape != p.shape:
                raise RuntimeError(f"unexpected model output shapes {p.shape}, {a.shape}; expected (n, {k})")
            if not (np.isfinite(p).all() and np.isfinite(a).all()):
                raise RuntimeError("model produced NaN/inf for valid input; check the checkpoint and input scaling")
            idx = np.flatnonzero(ok) + start
            probs[idx], abundance[idx], class_index[idx] = p, a, p.argmax(axis=1)

        names = np.array(self.mineral_names + ["invalid"], dtype=object)
        result = {
            "class_index": class_index,
            "mineral": names[class_index],
            "confidence": probs.max(axis=1),
            "class_probs": probs,
            "abundance": abundance,
            "valid": valid,
            "mineral_names": list(self.mineral_names),
            "wavelengths": self.wavelengths.copy(),
        }
        if top_k:
            order = np.argsort(-probs, axis=1, kind="stable")[:, :top_k].astype(np.int32)
            order[~valid] = INVALID
            score = np.take_along_axis(probs, np.maximum(order, 0), axis=1)
            score[~valid] = np.nan
            result.update({"top_k_index": order, "top_k_mineral": names[order], "top_k_score": score})
        return result


def summarize(result, i=0):
    """One sample from a predict() result as a plain dict."""
    out = {
        "valid": bool(result["valid"][i]),
        "mineral": str(result["mineral"][i]),
        "confidence": float(result["confidence"][i]),
        "abundances": dict(zip(result["mineral_names"], result["abundance"][i].tolist())),
    }
    if "top_k_mineral" in result:
        out["top_k"] = [{"mineral": str(m), "score": float(sc)}
                        for m, sc in zip(result["top_k_mineral"][i], result["top_k_score"][i])]
    return out


def predict_pixels(model, pixels, *, batch_size=8192, device=None):
    """nn_main glue: run a loaded SpectralTransformer over (N, L) preprocessed pixels.

    Returns {"class_ids": (N,), "abundances": (N, M), "confidence": (N,)}.
    """
    model.eval()
    dev = device or next(model.parameters()).device
    x = np.ascontiguousarray(np.asarray(pixels, dtype=np.float32))
    n, m = x.shape[0], model.config.num_minerals
    class_ids = np.empty(n, dtype=np.int64)
    abundances = np.empty((n, m), dtype=np.float32)
    confidence = np.empty(n, dtype=np.float32)
    with torch.no_grad():
        for s in range(0, n, batch_size):
            out = model(torch.as_tensor(x[s:s + batch_size], device=dev))
            probs = torch.softmax(out.class_logits, dim=1)
            class_ids[s:s + batch_size] = out.class_logits.argmax(1).cpu().numpy()
            abundances[s:s + batch_size] = out.abundances.cpu().numpy()
            confidence[s:s + batch_size] = probs.max(dim=1).values.cpu().numpy()
    return {"class_ids": class_ids, "abundances": abundances, "confidence": confidence}
