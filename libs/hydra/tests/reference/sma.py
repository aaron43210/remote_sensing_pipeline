"""Spectral Mixture Analysis: constrained linear unmixing.

Default is fully constrained (abundance >= 0, sum ~= 1). Pixels and endmembers must
share the same wavelength positions and the same kind of values (e.g. original
reflectance); nothing is resampled here.
"""
import numpy as np

SUM_WEIGHT = 1e3  # strength of the sum-to-one row; larger = sum closer to 1


def _as_2d(spectra, name):
    a = np.asarray(spectra, dtype=float)
    if a.ndim == 1:
        a = a[None, :]
    if a.ndim != 2 or a.shape[1] == 0:
        raise ValueError(f"{name} must be (n_bands,) or (n_spectra, n_bands)")
    return a


def unmix(pixels, endmembers, non_negative=True, sum_to_one=True):
    """Estimate abundance fractions of each endmember in each pixel.

    pixels:      (n_bands,) or (n_pixels, n_bands)
    endmembers:  (n_endmembers, n_bands)
    Returns a dict:
      abundances:    (n_pixels, n_endmembers); NaN rows for invalid pixels
      rmse:          (n_pixels,) root-mean-square residual, in input units
      abundance_sum: (n_pixels,) sum of abundances (check against 1)
    Sum-to-one is enforced with a heavily weighted extra equation, so the sum is
    very close to 1 but not exactly 1.
    """
    p = _as_2d(pixels, "pixels")
    em = _as_2d(endmembers, "endmembers")
    n_pixels, n_bands = p.shape
    n_em = em.shape[0]

    if em.shape[1] != n_bands:
        raise ValueError(
            f"band mismatch: pixels have {n_bands} bands, endmembers have {em.shape[1]}"
        )
    if not np.isfinite(em).all():
        raise ValueError("endmembers contain NaN/inf")
    if n_em > n_bands:
        raise ValueError(f"{n_em} endmembers but only {n_bands} bands")
    if np.linalg.matrix_rank(em) < n_em:
        raise ValueError("endmembers are linearly dependent; abundances not unique")

    E = em.T  # (n_bands, n_endmembers)
    valid = np.isfinite(p).all(axis=1)
    abundances = np.full((n_pixels, n_em), np.nan)
    rmse = np.full(n_pixels, np.nan)

    if valid.any():
        P = p[valid].T  # (n_bands, n_valid)
        A, B = E, P
        if sum_to_one:
            delta = SUM_WEIGHT * np.abs(E).max()
            A = np.vstack([E, np.full((1, n_em), delta)])
            B = np.vstack([P, np.full((1, P.shape[1]), delta)])

        if non_negative:
            from scipy.optimize import nnls
            import os
            from concurrent.futures import ThreadPoolExecutor

            n_cols = B.shape[1]
            X = np.full((n_em, n_cols), np.nan)

            if n_cols > 50:
                workers = min(16, os.cpu_count() or 4)
                def _solve(col_idx):
                    try:
                        return col_idx, nnls(A, B[:, col_idx])[0]
                    except RuntimeError:
                        return col_idx, None

                with ThreadPoolExecutor(max_workers=workers) as executor:
                    for col_idx, sol in executor.map(_solve, range(n_cols)):
                        if sol is not None:
                            X[:, col_idx] = sol
            else:
                for j in range(n_cols):
                    try:
                        X[:, j] = nnls(A, B[:, j])[0]
                    except RuntimeError:
                        pass
        else:
            X = np.linalg.lstsq(A, B, rcond=None)[0]

        abundances[valid] = X.T
        rmse[valid] = np.sqrt(np.mean((P - E @ X) ** 2, axis=0))

    return {
        "abundances": abundances,
        "rmse": rmse,
        "abundance_sum": abundances.sum(axis=1),
    }


def abundance_dict(abundances, names):
    """Turn one pixel's abundance vector into {mineral: fraction}."""
    a = np.asarray(abundances, dtype=float)
    if a.ndim != 1 or a.size != len(names):
        raise ValueError("abundances must be 1-D with one value per name")
    return dict(zip(names, a.tolist()))
