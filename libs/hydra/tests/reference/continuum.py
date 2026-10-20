"""Continuum removal for reflectance spectra (upper convex hull method)."""
import numpy as np

MIN_VALID_BANDS = 3


def _check_wavelengths(wavelengths):
    w = np.asarray(wavelengths, dtype=float)
    if w.ndim != 1 or w.size < MIN_VALID_BANDS:
        raise ValueError(f"wavelengths must be 1-D with >= {MIN_VALID_BANDS} values")
    if not np.all(np.isfinite(w)):
        raise ValueError("wavelengths contain NaN/inf")
    if np.any(np.diff(w) <= 0):
        raise ValueError("wavelengths must be strictly increasing")
    return w


def _check_reflectance(w, reflectance):
    r = np.asarray(reflectance, dtype=float)
    if r.shape != w.shape:
        raise ValueError(f"reflectance shape {r.shape} != wavelengths shape {w.shape}")
    return r


def _upper_hull(x, y):
    """Indices of the upper convex hull of points sorted by x."""
    hull = []
    for i in range(len(x)):
        while len(hull) >= 2:
            a, b = hull[-2], hull[-1]
            cross = (x[b] - x[a]) * (y[i] - y[a]) - (y[b] - y[a]) * (x[i] - x[a])
            if cross >= 0:  # not a clockwise turn -> b is under the hull
                hull.pop()
            else:
                break
        hull.append(i)
    return hull


def _continuum(w, r):
    """Continuum for validated inputs; NaN where r is NaN. None if too few bands."""
    valid = np.isfinite(r)
    if valid.sum() < MIN_VALID_BANDS:
        return None
    wv, rv = w[valid], r[valid]
    idx = _upper_hull(wv, rv)
    continuum = np.full(w.shape, np.nan)
    continuum[valid] = np.interp(wv, wv[idx], rv[idx])
    return continuum


def _divide(r, continuum):
    out = np.full(r.shape, np.nan)
    ok = np.isfinite(continuum) & (continuum > 0)
    out[ok] = r[ok] / continuum[ok]
    return out


def compute_continuum(wavelengths, reflectance):
    """Return the continuum curve (same length as input; NaN at invalid bands)."""
    w = _check_wavelengths(wavelengths)
    r = _check_reflectance(w, reflectance)
    continuum = _continuum(w, r)
    if continuum is None:
        raise ValueError(f"need at least {MIN_VALID_BANDS} valid (finite) bands")
    return continuum


def continuum_remove(wavelengths, reflectance):
    """Continuum-remove one spectrum.

    Returns (wavelengths, continuum_removed); wavelengths are returned unchanged.
    """
    w = _check_wavelengths(wavelengths)
    r = _check_reflectance(w, reflectance)
    continuum = _continuum(w, r)
    if continuum is None:
        raise ValueError(f"need at least {MIN_VALID_BANDS} valid (finite) bands")
    return w.copy(), _divide(r, continuum)


def continuum_remove_many(wavelengths, spectra):
    """Continuum-remove many spectra, shape (n_spectra, n_bands).

    Spectra with too few valid bands come back as all-NaN rows.
    Returns (wavelengths, continuum_removed) with the same shape as the input.
    """
    w = _check_wavelengths(wavelengths)
    s = np.asarray(spectra, dtype=float)
    if s.ndim != 2 or s.shape[1] != w.size:
        raise ValueError(f"spectra must have shape (n, {w.size}), got {s.shape}")
    out = np.full(s.shape, np.nan)
    for i, r in enumerate(s):
        continuum = _continuum(w, r)
        if continuum is not None:
            out[i] = _divide(r, continuum)
    return w.copy(), out


def remove_continuum_cube(cube, wavelengths):
    """Continuum-remove a (height, width, bands) reflectance cube; same shape out.

    Adapter used by the NN inference path (nn_main.py).
    """
    arr = np.asarray(cube, dtype=float)
    if arr.ndim != 3:
        raise ValueError("cube must be (height, width, bands)")
    h, w, b = arr.shape
    out = continuum_remove_many(wavelengths, arr.reshape(h * w, b))[1]
    return out.reshape(h, w, b)

