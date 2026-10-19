"""Heuristic quality scores for SAM / SMA results.

Scores are in [0, 1] (1 = best, NaN = invalid input). They are quality
indicators, NOT calibrated probabilities of correctness.
"""
import numpy as np


def _check_positive(value, name):
    if not (np.isfinite(value) and value > 0):
        raise ValueError(f"{name} must be a positive finite number")


def sam_scores(angles, max_angle):
    """Quality from SAM angles, shape (n_pixels, n_refs), in radians.

    angle_score: 1 - best_angle / max_angle  (0 if best_angle >= max_angle)
    separation:  (second - best) / second    (0 = tie, near 1 = clear winner)
    quality:     min(angle_score, separation)
    Needs at least 2 valid references, otherwise separation/quality are NaN.
    """
    a = np.asarray(angles, dtype=float)
    if a.ndim != 2 or a.shape[1] < 1:
        raise ValueError("angles must be 2-D (n_pixels, n_refs)")
    _check_positive(max_angle, "max_angle")

    n_pixels, n_refs = a.shape
    if n_refs >= 2:
        part = np.partition(a, 1, axis=1)  # NaNs are placed last
        best, second = part[:, 0], part[:, 1]
    else:
        best, second = a[:, 0], np.full(n_pixels, np.nan)

    angle_score = np.clip(1.0 - best / max_angle, 0.0, 1.0)

    separation = np.full(n_pixels, np.nan)
    ok = np.isfinite(best) & np.isfinite(second)
    b, s = best[ok], second[ok]
    separation[ok] = np.clip(np.divide(s - b, s, out=np.zeros_like(s), where=s > 0), 0.0, 1.0)

    return {
        "angle_score": angle_score,
        "separation": separation,
        "quality": np.minimum(angle_score, separation),
    }


def sma_quality(rmse, max_rmse):
    """Quality from SMA residual: 1 - rmse / max_rmse, clipped to [0, 1].

    max_rmse is in the same units as the spectra given to SMA.
    """
    _check_positive(max_rmse, "max_rmse")
    r = np.asarray(rmse, dtype=float)
    return np.clip(1.0 - r / max_rmse, 0.0, 1.0)


def combine_quality(*scores):
    """Overall quality = the weakest of the given scores (NaN if any is NaN)."""
    if not scores:
        raise ValueError("provide at least one score array")
    arrays = [np.asarray(s, dtype=float) for s in scores]
    if any(a.shape != arrays[0].shape for a in arrays):
        raise ValueError("all score arrays must have the same shape")
    return np.minimum.reduce(arrays)
