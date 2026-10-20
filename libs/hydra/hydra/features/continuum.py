"""
Upper-convex-hull continuum removal, compiled with numba.

Numerically identical to services/mineral_analysis/continuum.py (same hull
rule `cross >= 0 -> pop`, same linear interpolation, NaN where the continuum is
not positive). That module loops in Python; this one does the same per pixel
in machine code, which is what makes whole-scene inference practical.
"""

import numpy as np
from numba import njit, prange

MIN_VALID_BANDS = 3


@njit(cache=True)
def _cr_one(w, r, out, hull, wv, rv):
    n = w.size
    m = 0
    for i in range(n):
        if np.isfinite(r[i]):
            wv[m] = w[i]
            rv[m] = r[i]
            m += 1
    for i in range(n):
        out[i] = np.nan
    if m < MIN_VALID_BANDS:
        return

    # upper hull of (wv, rv), points already sorted by wavelength
    h = 0
    for i in range(m):
        while h >= 2:
            a = hull[h - 2]
            b = hull[h - 1]
            cross = (wv[b] - wv[a]) * (rv[i] - rv[a]) - (rv[b] - rv[a]) * (wv[i] - wv[a])
            if cross >= 0:
                h -= 1
            else:
                break
        hull[h] = i
        h += 1

    # interpolate the hull at every valid band, then divide
    seg = 0
    for i in range(n):
        if not np.isfinite(r[i]):
            continue
        x = w[i]
        while seg < h - 2 and wv[hull[seg + 1]] < x:
            seg += 1
        x0 = wv[hull[seg]]
        x1 = wv[hull[seg + 1]] if h > 1 else x0
        y0 = rv[hull[seg]]
        y1 = rv[hull[seg + 1]] if h > 1 else y0
        if x <= x0:
            c = y0
        elif x >= x1:
            c = y1
        else:
            c = y0 + (x - x0) * (y1 - y0) / (x1 - x0)
        if np.isfinite(c) and c > 0:
            out[i] = r[i] / c


@njit(parallel=True, cache=True)
def _cr_many(w, spectra, out):
    n_pix, n_b = spectra.shape
    for p in prange(n_pix):
        hull = np.empty(n_b, dtype=np.int64)
        wv = np.empty(n_b)
        rv = np.empty(n_b)
        _cr_one(w, spectra[p], out[p], hull, wv, rv)


def continuum_remove_many(wavelengths, spectra):
    """(n, bands) reflectance -> (n, bands) continuum-removed; NaN rows pass through."""
    w = np.ascontiguousarray(wavelengths, dtype=np.float64)
    s = np.ascontiguousarray(np.atleast_2d(spectra), dtype=np.float64)
    if s.shape[1] != w.size:
        raise ValueError(f"spectra have {s.shape[1]} bands, wavelengths {w.size}")
    if np.any(np.diff(w) <= 0):
        raise ValueError("wavelengths must be strictly increasing")
    out = np.empty_like(s)
    _cr_many(w, s, out)
    return out
