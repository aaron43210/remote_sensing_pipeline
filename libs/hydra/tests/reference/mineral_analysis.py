"""High-level mineral analysis: alignment -> continuum removal -> SAM -> SMA -> quality.

Coordinates the existing modules only; no algorithm lives here. Errors from the
underlying modules are re-raised with the failing stage named.
"""
from dataclasses import dataclass
from typing import Optional

import numpy as np

import confidence
import continuum
import sam
import sma
import wavelength_alignment


def _stage(name, fn, *args, **kwargs):
    """Run one processing stage; add the stage name to any error."""
    try:
        return fn(*args, **kwargs)
    except ValueError as e:
        raise ValueError(f"{name}: {e}") from e
    except Exception as e:
        raise RuntimeError(f"{name} failed: {e}") from e


@dataclass
class AnalysisResult:
    """Results for a batch of n_pixels spectra (one entry per pixel)."""
    class_index: np.ndarray  # reference index, or sam.UNKNOWN / sam.INVALID
    mineral: np.ndarray  # mineral name, "unknown" or "invalid"
    min_angle: np.ndarray  # best SAM angle (radians)
    angles: np.ndarray  # (n_pixels, n_minerals) SAM angle to every reference
    abundances: np.ndarray  # (n_pixels, n_minerals) SMA fractions
    rmse: np.ndarray  # SMA reconstruction error
    abundance_sum: np.ndarray  # sum of abundances (check against 1)
    names: list
    quality: Optional[dict] = None  # angle_score, separation, sam, sma, overall

    def summary(self, i=0):
        """One pixel as a plain dict."""
        out = {
            "mineral": str(self.mineral[i]),
            "angle": float(self.min_angle[i]),
            "abundances": sma.abundance_dict(self.abundances[i], self.names),
            "rmse": float(self.rmse[i]),
        }
        if self.quality is not None:
            out["quality"] = float(self.quality["overall"][i])
        return out


class MineralAnalyzer:
    """Align the library to the image wavelengths once, then analyze pixel batches.

    image_wavelengths: wavelengths of the pixel spectra that will be passed to analyze().
    names, library_wavelengths, library_spectra: the loaded reference library,
        (n_minerals,), (n_library_bands,), (n_minerals, n_library_bands).
    Quality is computed only when both sam_max_angle and sma_max_rmse are set.
    Resampling settings (method, max_gap, strict) are passed to wavelength_alignment.
    """

    def __init__(self, image_wavelengths, names, library_wavelengths, library_spectra, *,
                 method="linear", max_gap=None, strict=False,
                 use_continuum_removal=True, non_negative=True, sum_to_one=True,
                 sam_max_angle=None, sma_max_rmse=None):
        spectra = np.asarray(library_spectra, dtype=float)
        if len(names) == 0 or spectra.size == 0:
            raise ValueError("no reference spectra provided")
        if spectra.ndim != 2 or spectra.shape[0] != len(names):
            raise ValueError("library_spectra must be (n_minerals, n_library_bands), one row per name")

        aligned = _stage("wavelength alignment", wavelength_alignment.align_library_to_image,
                         image_wavelengths, library_wavelengths, spectra,
                         method=method, max_gap=max_gap, strict=strict)
        self.names = list(names)
        self.n_input_bands = len(image_wavelengths)
        self.band_index = aligned.band_index  # image bands actually used
        self.wavelengths = aligned.wavelengths  # their wavelengths (unchanged values)
        self.library = aligned.spectra  # original-reflectance references (for SMA)
        self.alignment_method = aligned.method

        self.use_continuum_removal = use_continuum_removal
        self.sam_refs = self.library
        if use_continuum_removal:
            self.sam_refs = _stage("continuum removal (library)", continuum.continuum_remove_many,
                                   self.wavelengths, self.library)[1]
        self.non_negative, self.sum_to_one = non_negative, sum_to_one
        self.sam_max_angle, self.sma_max_rmse = sam_max_angle, sma_max_rmse

    @property
    def has_quality(self):
        return self.sam_max_angle is not None and self.sma_max_rmse is not None

    def analyze(self, pixels):
        """Analyze one spectrum (n_bands,) or a batch (n_pixels, n_bands).

        Columns must match image_wavelengths. Pass large images in batches.
        SAM uses continuum-removed spectra (if enabled); SMA uses original reflectance.
        """
        p = np.asarray(pixels, dtype=float)
        if p.ndim == 1:
            p = p[None, :]
        if p.ndim != 2 or p.shape[0] == 0:
            raise ValueError("pixels must be non-empty, shape (n_bands,) or (n_pixels, n_bands)")
        if p.shape[1] != self.n_input_bands:
            raise ValueError(f"pixels have {p.shape[1]} bands but image_wavelengths has {self.n_input_bands}")
        p = p[:, self.band_index]  # keep only the aligned bands

        sam_in = p
        if self.use_continuum_removal:
            sam_in = _stage("continuum removal", continuum.continuum_remove_many, self.wavelengths, p)[1]
        sam_out = _stage("SAM", sam.classify_sam, sam_in, self.sam_refs, self.names, self.sam_max_angle)
        sma_out = _stage("SMA", sma.unmix, p, self.library, self.non_negative, self.sum_to_one)

        quality = _stage("quality", self._quality, sam_out, sma_out) if self.has_quality else None
        return AnalysisResult(
            class_index=sam_out["class_index"], mineral=sam_out["mineral"],
            min_angle=sam_out["min_angle"], angles=sam_out["angles"],
            abundances=sma_out["abundances"], rmse=sma_out["rmse"],
            abundance_sum=sma_out["abundance_sum"], names=self.names, quality=quality,
        )

    def _quality(self, sam_out, sma_out):
        s = confidence.sam_scores(sam_out["angles"], self.sam_max_angle)
        q_sma = confidence.sma_quality(sma_out["rmse"], self.sma_max_rmse)
        return {
            "angle_score": s["angle_score"], "separation": s["separation"],
            "sam": s["quality"], "sma": q_sma,
            "overall": confidence.combine_quality(s["quality"], q_sma),
        }

