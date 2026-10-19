"""Wavelength alignment between satellite bands and library spectra.

Spectra are compared by actual wavelength values, never by band number.
Satellite spectra are not modified: library spectra are resampled onto the
satellite wavelengths (treated as band centres). Both wavelength arrays must be
in the same units; this module cannot detect a unit mismatch other than by
finding no overlap.
"""
import warnings
from typing import NamedTuple

import numpy as np

METHODS = ("linear", "nearest")


class Alignment(NamedTuple):
    band_index: np.ndarray  # indices of the satellite bands that were kept
    wavelengths: np.ndarray  # satellite wavelengths used (values unchanged)
    spectra: np.ndarray  # library spectra on those wavelengths, (n_spectra, n_used)
    method: str


def check_wavelengths(wavelengths, name="wavelengths", min_size=2):
    """Return wavelengths as a float array; must be 1-D, finite, strictly increasing."""
    w = np.asarray(wavelengths, dtype=float)
    if w.ndim != 1 or w.size < min_size:
        raise ValueError(f"{name} must be 1-D with >= {min_size} values")
    if not np.all(np.isfinite(w)):
        raise ValueError(f"{name} contain NaN/inf")
    if np.any(np.diff(w) <= 0):
        raise ValueError(f"{name} must be strictly increasing")
    return w


def common_range(wl_a, wl_b):
    """(low, high) wavelength range covered by both arrays; error if none."""
    a = check_wavelengths(wl_a, "first wavelengths")
    b = check_wavelengths(wl_b, "second wavelengths")
    lo, hi = max(a[0], b[0]), min(a[-1], b[-1])
    if lo >= hi:
        raise ValueError(
            f"no wavelength overlap: {a[0]:.6g}-{a[-1]:.6g} vs {b[0]:.6g}-{b[-1]:.6g}; "
            "check that both use the same units"
        )
    return lo, hi


def _resample_one(x, y, target, method, max_gap):
    """One spectrum onto target; NaN where it cannot be done without extrapolating."""
    ok = np.isfinite(y)
    x, y = x[ok], y[ok]
    out = np.full(target.shape, np.nan)
    if x.size < 2:
        return out

    # nearest valid library sample for every target wavelength
    pos = np.searchsorted(x, target)
    left, right = np.clip(pos - 1, 0, x.size - 1), np.clip(pos, 0, x.size - 1)
    nearest = np.where(np.abs(target - x[left]) <= np.abs(x[right] - target), left, right)

    good = (target >= x[0]) & (target <= x[-1])
    if max_gap is not None:
        good &= np.abs(x[nearest] - target) <= max_gap

    if method == "linear":
        out[good] = np.interp(target[good], x, y)
    else:
        out[good] = y[nearest[good]]
    return out


def resample_spectra(source_wl, spectra, target_wl, method="linear", max_gap=None):
    """Resample spectra from source_wl onto target_wl using wavelength values.

    method:  "linear"  - interpolate between the neighbouring valid samples
             "nearest" - take the nearest valid sample
    max_gap: largest allowed distance (wavelength units) from a target wavelength
             to the nearest valid sample; None = no limit (gaps are then bridged).
    Targets outside a spectrum's valid range, or beyond max_gap, become NaN
    (never extrapolated). Returns (n_spectra, n_target), or (n_target,) for 1-D input.
    """
    if method not in METHODS:
        raise ValueError(f"method must be one of {METHODS}, got {method!r}")
    if max_gap is not None and not max_gap > 0:
        raise ValueError("max_gap must be positive or None")
    x = check_wavelengths(source_wl, "source wavelengths")
    t = check_wavelengths(target_wl, "target wavelengths")

    s = np.asarray(spectra, dtype=float)
    single = s.ndim == 1
    s = np.atleast_2d(s)
    if s.ndim != 2 or s.shape[1] != x.size:
        raise ValueError(f"spectra must have {x.size} values per spectrum (one per source wavelength)")

    out = np.vstack([_resample_one(x, row, t, method, max_gap) for row in s])
    return out[0] if single else out


def align_cube(cube, wavelengths, target_wavelengths, method="linear", max_gap=None):
    """Resample a (height, width, bands) cube onto target_wavelengths -> (H, W, L).

    Adapter used by the NN inference path (nn_main.py).
    """
    arr = np.asarray(cube, dtype=float)
    if arr.ndim != 3:
        raise ValueError("cube must be (height, width, bands)")
    h, w, b = arr.shape
    t = check_wavelengths(target_wavelengths, "target wavelengths")
    out = resample_spectra(wavelengths, arr.reshape(h * w, b), t, method=method, max_gap=max_gap)
    return out.reshape(h, w, t.size)


def align_library_to_image(image_wl, library_wl, library_spectra,
                           method="linear", max_gap=None, strict=False, min_bands=3):
    """Put library spectra on the satellite wavelengths.

    Keeps only satellite bands where every library spectrum has a valid value.
    Dropped bands trigger a warning, or an error if strict=True.
    min_bands=3 is the minimum continuum removal needs.
    Returns Alignment(band_index, wavelengths, spectra, method); use band_index to
    pick the matching bands from the satellite data.
    """
    img = check_wavelengths(image_wl, "image wavelengths")
    lib = check_wavelengths(library_wl, "library wavelengths")
    common_range(img, lib)  # clear error for no overlap / unit mismatch

    resampled = np.atleast_2d(resample_spectra(lib, library_spectra, img, method, max_gap))
    keep = np.isfinite(resampled).all(axis=0)

    dropped = int((~keep).sum())
    if dropped:
        msg = f"{dropped} of {img.size} image bands dropped: not covered by every library spectrum"
        if strict:
            raise ValueError(msg)
        warnings.warn(msg, stacklevel=2)
    if keep.sum() < min_bands:
        raise ValueError(f"only {int(keep.sum())} usable common bands; need at least {min_bands}")

    idx = np.flatnonzero(keep)
    return Alignment(idx, img[idx], resampled[:, idx], method)
