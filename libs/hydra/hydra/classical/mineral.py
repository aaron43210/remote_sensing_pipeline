"""
The "normal method" for minerals: SAM + SMA + quality.

A faithful port of services/mineral_analysis (mineral_analysis.MineralAnalyzer,
sam.classify_sam, sma.unmix, confidence.*), restricted to the EMIT good-band
grid. It is the TEACHER: it labels training data and is the reference every
HydraNet mineral map is checked against. It never runs in the online path
except for shadow verification on a pixel sample.
"""

import os
from concurrent.futures import ThreadPoolExecutor

import numpy as np
from scipy.optimize import nnls

from .. import spec
from ..features.mineral import MineralFeatures

INVALID = -2


def sma_unmix(pixels, endmembers, sum_weight=spec.SMA_SUM_WEIGHT):
    """Fully constrained unmixing (NNLS + heavily weighted sum-to-one row)."""
    p = np.atleast_2d(np.asarray(pixels, dtype=float))
    E = np.asarray(endmembers, dtype=float).T                  # (bands, k)
    n_em = E.shape[1]
    abund = np.full((p.shape[0], n_em), np.nan)
    rmse = np.full(p.shape[0], np.nan)
    valid = np.isfinite(p).all(axis=1)
    if not valid.any():
        return abund, rmse

    delta = sum_weight * np.abs(E).max()
    A = np.vstack([E, np.full((1, n_em), delta)])
    P = p[valid].T
    B = np.vstack([P, np.full((1, P.shape[1]), delta)])

    def solve(j):
        try:
            return nnls(A, B[:, j])[0]
        except RuntimeError:
            return np.full(n_em, np.nan)

    workers = min(16, os.cpu_count() or 4) if B.shape[1] > 50 else 1
    if workers > 1:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            X = np.column_stack(list(ex.map(solve, range(B.shape[1]), chunksize=256)))
    else:
        X = np.column_stack([solve(j) for j in range(B.shape[1])])
    abund[valid] = X.T
    rmse[valid] = np.sqrt(np.mean((P - E @ X) ** 2, axis=0))
    return abund, rmse


def quality(angles, rmse, max_angle=spec.SAM_MAX_ANGLE, max_rmse=spec.SMA_MAX_RMSE):
    """confidence.sam_scores + sma_quality + combine_quality."""
    part = np.partition(angles, 1, axis=1)
    best, second = part[:, 0], part[:, 1]
    angle_score = np.clip(1.0 - best / max_angle, 0.0, 1.0)
    separation = np.full(best.shape, np.nan)
    ok = np.isfinite(best) & np.isfinite(second)
    b, s = best[ok], second[ok]
    separation[ok] = np.clip(np.divide(s - b, s, out=np.zeros_like(s), where=s > 0), 0, 1)
    q_sam = np.minimum(angle_score, separation)
    q_sma = np.clip(1.0 - rmse / max_rmse, 0.0, 1.0)
    return np.minimum(q_sam, q_sma)


class MineralTeacher:
    """SAM class (with 'unknown'), SMA abundances, quality, for good-band spectra."""

    def __init__(self, features=None):
        self.features = features or MineralFeatures()

    def label(self, reflectance, x=None):
        """
        reflectance: (n, 255) on the EMIT good-band grid.
        x: the 530 features if already computed (saves the continuum removal).
        Returns dict(class_index (n,) int, abundances (n, 12), quality (n,), min_angle (n,)).
        """
        r = np.atleast_2d(np.asarray(reflectance, dtype=float))
        if x is None:
            x = self.features.transform(r)
        angles = x[:, spec.SAM_SLICE].astype(float)
        valid = ~np.isnan(angles).all(axis=1)
        cls = np.full(r.shape[0], INVALID, dtype=np.int64)
        min_angle = np.full(r.shape[0], np.nan)
        if valid.any():
            best = np.nanargmin(angles[valid], axis=1)
            cls[valid] = best
            min_angle[valid] = angles[valid][np.arange(best.size), best]
        cls[valid & (min_angle > spec.SAM_MAX_ANGLE)] = spec.UNKNOWN_CLASS
        abund, rmse = sma_unmix(r, self.features.endmembers)
        return {"class_index": cls, "abundances": abund, "rmse": rmse,
                "quality": quality(angles, rmse), "min_angle": min_angle}
