"""Spectral Angle Mapper (SAM). All angles are in radians.

Spectra must already share the same wavelength positions; nothing is resampled here.
"""
import numpy as np

UNKNOWN = -1  # best angle is above max_angle
INVALID = -2  # no valid angle could be computed (NaN / zero-vector input)


def _as_2d(spectra, name):
    a = np.asarray(spectra, dtype=float)
    if a.ndim == 1:
        a = a[None, :]
    if a.ndim != 2 or a.shape[1] == 0:
        raise ValueError(f"{name} must be (n_bands,) or (n_spectra, n_bands)")
    return a


def _unit_vectors(spectra):
    """Scale each row to length 1. Returns (unit, ok); bad rows stay zero and ok=False."""
    finite = np.isfinite(spectra).all(axis=1)
    clean = np.where(finite[:, None], spectra, 0.0)
    norms = np.linalg.norm(clean, axis=1)
    ok = finite & (norms > 0)
    unit = np.zeros_like(clean)
    unit[ok] = clean[ok] / norms[ok, None]
    return unit, ok


def spectral_angle(a, b):
    """Angle between two 1-D spectra. NaN if either is invalid or zero."""
    a, b = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    if a.ndim != 1 or b.ndim != 1:
        raise ValueError("spectral_angle expects two 1-D spectra")
    return float(spectral_angles(a, b)[0, 0])


def spectral_angles(pixels, references):
    """Angles between every pixel and every reference.

    pixels:     (n_bands,) or (n_pixels, n_bands)
    references: (n_bands,) or (n_refs, n_bands)
    Returns (n_pixels, n_refs); NaN where a pixel or reference is invalid.
    """
    p = _as_2d(pixels, "pixels")
    r = _as_2d(references, "references")
    if p.shape[1] != r.shape[1]:
        raise ValueError(
            f"band mismatch: pixels have {p.shape[1]} bands, references have {r.shape[1]}"
        )
    p_unit, p_ok = _unit_vectors(p)
    r_unit, r_ok = _unit_vectors(r)
    angles = np.arccos(np.clip(p_unit @ r_unit.T, -1.0, 1.0))
    angles[~p_ok, :] = np.nan
    angles[:, ~r_ok] = np.nan
    return angles


def classify_sam(pixels, references, names=None, max_angle=None):
    """Assign each pixel to the reference with the smallest spectral angle.

    max_angle: optional radians; pixels whose best angle is larger become UNKNOWN.
    Returns a dict:
      class_index: (n_pixels,) reference index, or UNKNOWN / INVALID
      min_angle:   (n_pixels,) smallest angle (NaN if INVALID)
      angles:      (n_pixels, n_refs) angle to every reference
      mineral:     (n_pixels,) names, "unknown" or "invalid" (only if names given)
    """
    if max_angle is not None and not max_angle > 0:
        raise ValueError("max_angle must be a positive number (radians)")
    angles = spectral_angles(pixels, references)
    n_pixels, n_refs = angles.shape
    if names is not None and len(names) != n_refs:
        raise ValueError(f"got {len(names)} names for {n_refs} references")

    class_index = np.full(n_pixels, INVALID, dtype=np.int32)
    min_angle = np.full(n_pixels, np.nan)
    valid = ~np.isnan(angles).all(axis=1)
    if valid.any():
        best = np.nanargmin(angles[valid], axis=1)
        class_index[valid] = best
        min_angle[valid] = angles[valid][np.arange(best.size), best]
    if max_angle is not None:
        class_index[valid & (min_angle > max_angle)] = UNKNOWN

    result = {"class_index": class_index, "min_angle": min_angle, "angles": angles}
    if names is not None:
        names = np.asarray(names, dtype=object)
        mineral = np.full(n_pixels, "invalid", dtype=object)
        mineral[class_index == UNKNOWN] = "unknown"
        hit = class_index >= 0
        mineral[hit] = names[class_index[hit]]
        result["mineral"] = mineral
    return result


class SpectralAngleMapper:
    """Wrapper class providing single-pixel SAM methods."""

    def compute_angle(self, s1, s2):
        return spectral_angle(s1, s2)

    def classify_pixel(self, pixel, reference_dict):
        names = list(reference_dict.keys())
        refs = np.array([reference_dict[n] for n in names])
        angles = spectral_angles(pixel, refs)[0]
        best_idx = int(np.argmin(angles))
        return {
            "mineral": names[best_idx],
            "angle": float(angles[best_idx]),
            "angles": dict(zip(names, angles.tolist()))
        }
