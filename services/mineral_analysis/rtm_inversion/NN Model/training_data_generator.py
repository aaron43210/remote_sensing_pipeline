"""Generates NN training spectra: mineral variations + synthetic mixtures.

Reuses spectral_library.py (loading), wavelength_alignment.py (alignment) and
continuum.py (continuum removal); no new spectral algorithms here. SAM/SMA
teacher targets are generated separately, by teacher_generator.py.

Each sample has:
  reflectance - raw mixed/perturbed reflectance (what SMA-style targets need)
  spectrum    - continuum-removed version (the NN input, matching inference
                preprocessing); equals reflectance if apply_continuum_removal=False
  abundance   - true fraction per mineral in MINERAL_NAMES order (>=0, sums to 1)
  is_pure     - True for single-mineral samples
  dominant_class - index (into mineral_names) of the largest abundance
"""
import numpy as np

# The service pipeline modules live three levels up (services/mineral_analysis);
# put that dir on sys.path so `import continuum` etc. resolve from this subfolder.
import sys
from pathlib import Path
_SERVICE_DIR = str(Path(__file__).resolve().parents[2])
if _SERVICE_DIR not in sys.path:
    sys.path.insert(0, _SERVICE_DIR)

import continuum
import spectral_library
import wavelength_alignment

NOISE_STD_RANGE = (0.0, 0.01)      # random per-band noise, in reflectance units
SCALE_RANGE = (0.95, 1.05)         # random overall brightness factor
SMOOTH_AMPLITUDE_RANGE = (0.0, 0.02)  # random smooth (low-frequency) drift


def _select_minerals(names, spectra, wanted):
    """Pick `wanted` minerals from the library case-insensitively.

    Keeps the requested display names (e.g. "Quartz") as labels but uses the
    matching library spectra, so the config's capitalisation is preserved while
    the lowercase CSV library still resolves.
    """
    lower = {str(n).lower(): i for i, n in enumerate(names)}
    missing = sorted(w for w in wanted if str(w).lower() not in lower)
    if missing:
        raise ValueError(f"minerals not found in library: {missing}; available: {sorted(names)}")
    idx = [lower[str(w).lower()] for w in wanted]
    return list(wanted), np.asarray(spectra)[idx]


def load_endmembers(target_wavelengths, library_dir, mineral_names, method="linear", max_gap=None):
    """Load and align USGS endmembers to target_wavelengths.

    Returns (names, wavelengths, reflectance) with reflectance shape
    (n_minerals, n_target_bands); wavelengths may be a subset of target_wavelengths
    (see wavelength_alignment.align_library_to_image).
    """
    names, lib_wl, spectra = spectral_library.load_library(library_dir)
    names, spectra = _select_minerals(list(names), spectra, mineral_names)
    aligned = wavelength_alignment.align_library_to_image(
        target_wavelengths, lib_wl, spectra, method=method, max_gap=max_gap)
    return names, aligned.wavelengths, aligned.spectra


def _smooth_curve(rng, n_bands, amplitude):
    """Low-frequency random curve (a few smoothed random points) for drift-like noise."""
    if amplitude <= 0:
        return np.zeros(n_bands)
    n_knots = max(3, n_bands // 20)
    knots = rng.normal(0, amplitude, n_knots)
    return np.interp(np.arange(n_bands), np.linspace(0, n_bands - 1, n_knots), knots)


def _perturb(spectrum, rng):
    """Add noise, a brightness scale, and a smooth drift; clip to non-negative."""
    noise = rng.normal(0, rng.uniform(*NOISE_STD_RANGE), spectrum.shape)
    scale = rng.uniform(*SCALE_RANGE)
    drift = _smooth_curve(rng, spectrum.size, rng.uniform(*SMOOTH_AMPLITUDE_RANGE))
    return np.clip(spectrum * scale + noise + drift, 0.0, None)


def _pure_samples(endmembers, n_per_mineral, rng):
    n_minerals, n_bands = endmembers.shape
    reflectance = np.empty((n_minerals * n_per_mineral, n_bands))
    abundance = np.zeros((n_minerals * n_per_mineral, n_minerals))
    row = 0
    for m in range(n_minerals):
        for _ in range(n_per_mineral):
            reflectance[row] = _perturb(endmembers[m], rng)
            abundance[row, m] = 1.0
            row += 1
    return reflectance, abundance


def _mixture_samples(endmembers, n_mixtures, minerals_per_mixture, rng):
    n_minerals, n_bands = endmembers.shape
    lo, hi = minerals_per_mixture
    lo, hi = max(2, lo), min(n_minerals, hi)
    if lo > hi:
        raise ValueError(f"minerals_per_mixture {minerals_per_mixture} is incompatible with {n_minerals} minerals")

    reflectance = np.empty((n_mixtures, n_bands))
    abundance = np.zeros((n_mixtures, n_minerals))
    for i in range(n_mixtures):
        k = rng.integers(lo, hi + 1)
        idx = rng.choice(n_minerals, size=k, replace=False)
        fractions = rng.dirichlet(np.ones(k))  # non-negative, sums to 1
        abundance[i, idx] = fractions
        mix = fractions @ endmembers[idx]  # physical mixing in reflectance space
        reflectance[i] = _perturb(mix, rng)
    return reflectance, abundance


def generate_dataset(target_wavelengths, library_dir, mineral_names,
                     n_pure_per_mineral=50, n_mixtures=200, minerals_per_mixture=(2, 3),
                     apply_continuum_removal=True, method="linear", max_gap=None, seed=None):
    """Build a training set of pure + mixed spectra for the given minerals.

    Returns a dict: wavelengths, reflectance, spectrum, abundance, mineral_names,
    is_pure, dominant_class.
    """
    if len(mineral_names) < 2:
        raise ValueError("need at least 2 minerals to generate mixtures")
    rng = np.random.default_rng(seed)

    names, wl, endmembers = load_endmembers(target_wavelengths, library_dir, mineral_names, method, max_gap)
    pure_refl, pure_ab = _pure_samples(endmembers, n_pure_per_mineral, rng)
    mix_refl, mix_ab = _mixture_samples(endmembers, n_mixtures, minerals_per_mixture, rng)

    reflectance = np.vstack([pure_refl, mix_refl]).astype(np.float32)
    abundance = np.vstack([pure_ab, mix_ab]).astype(np.float32)
    is_pure = np.array([True] * len(pure_refl) + [False] * len(mix_refl))

    spectrum = reflectance
    if apply_continuum_removal:
        spectrum = continuum.continuum_remove_many(wl, reflectance)[1].astype(np.float32)

    return {
        "wavelengths": wl, "reflectance": reflectance, "spectrum": spectrum,
        "abundance": abundance, "mineral_names": names, "is_pure": is_pure,
        "dominant_class": abundance.argmax(axis=1),
    }


def save_dataset(path, dataset):
    np.savez(path, wavelengths=dataset["wavelengths"], reflectance=dataset["reflectance"],
             spectrum=dataset["spectrum"], abundance=dataset["abundance"],
             mineral_names=np.array(dataset["mineral_names"], dtype=object),
             is_pure=dataset["is_pure"], dominant_class=dataset["dominant_class"])


def load_dataset(path):
    with np.load(path, allow_pickle=True) as f:
        return {
            "wavelengths": f["wavelengths"], "reflectance": f["reflectance"],
            "spectrum": f["spectrum"], "abundance": f["abundance"],
            "mineral_names": f["mineral_names"].tolist(),
            "is_pure": f["is_pure"], "dominant_class": f["dominant_class"],
        }


def emit_good_wavelengths(band_metadata_path):
    """Good-quality EMIT band wavelengths (nm), ascending, from band_metadata.json."""
    import json
    meta = json.loads(Path(band_metadata_path).read_text(encoding="utf-8"))
    wl = [b["wavelength_nm"] for b in meta["bands"] if b.get("good_quality", True)]
    return np.array(sorted(float(w) for w in wl), dtype=float)


def generate_training_spectra(config):
    """nn_main glue: build the synthetic spectra bundle from an nn_config.NNConfig.

    Target grid = the EMIT good bands, so the trained model applies directly to
    the real cube after wavelength alignment + continuum removal.
    """
    target_wl = emit_good_wavelengths(config.band_metadata)
    return generate_dataset(
        target_wl, config.library_dir, list(config.mineral_names),
        n_pure_per_mineral=config.n_pure_per_mineral, n_mixtures=config.n_mixtures,
        minerals_per_mixture=tuple(config.minerals_per_mixture),
        apply_continuum_removal=config.apply_continuum_removal, seed=config.seed,
    )
