"""Load the USGS spectral library and align it to the EMIT good-band grid.

Thin wrapper over the service's own `spectral_library` and `wavelength_alignment`
modules so the dataset builder and the SAM/SMA reference use *identical*
endmembers and wavelengths.
"""
import json
import os
import sys
from pathlib import Path

import numpy as np

# The service modules live one directory up; make them importable.
_SERVICE_DIR = str(Path(__file__).resolve().parent.parent)
if _SERVICE_DIR not in sys.path:
    sys.path.insert(0, _SERVICE_DIR)

import spectral_library  # noqa: E402
import wavelength_alignment  # noqa: E402

BAND_METADATA = Path(_SERVICE_DIR) / "band_metadata.json"


def emit_band_table(metadata_path=BAND_METADATA):
    """Return (wavelengths_nm, good_mask) for every EMIT band from band_metadata.json."""
    meta = json.loads(Path(metadata_path).read_text(encoding="utf-8"))
    bands = meta["bands"]
    wl = np.array([b["wavelength_nm"] for b in bands], dtype=float)
    good = np.array([bool(b.get("good_quality", True)) for b in bands], dtype=bool)
    return wl, good


def load_aligned_library(library_dir, target_wl, minerals=None):
    """Load the library and resample it onto `target_wl` (nm).

    Returns (names, aligned) where aligned has shape (n_minerals, len(target_wl)).
    Refuses to extrapolate: every target wavelength must sit inside the library
    range (mirrors main._load_aligned_library so labels and dataset agree).
    """
    library_dir = Path(library_dir)
    if not library_dir.is_dir():
        raise FileNotFoundError(f"spectral library directory not found: {library_dir}")

    names, lib_wl, spectra = spectral_library.load_library(library_dir)
    lib_wl = np.asarray(lib_wl, dtype=float)
    target_wl = np.asarray(target_wl, dtype=float)

    if minerals:
        want = {str(m).strip().lower() for m in minerals}
        keep = [i for i, n in enumerate(names) if n.lower() in want]
        if not keep:
            raise ValueError(f"none of {minerals} found in library: {names}")
        names = [names[i] for i in keep]
        spectra = spectra[keep]

    if target_wl.min() < lib_wl.min() or target_wl.max() > lib_wl.max():
        raise ValueError(
            f"target wavelengths {target_wl.min():.4g}-{target_wl.max():.4g} nm fall outside "
            f"the library range {lib_wl.min():.4g}-{lib_wl.max():.4g} nm"
        )

    aligned = np.asarray(
        wavelength_alignment.resample_spectra(lib_wl, spectra, target_wl, method="linear"),
        dtype=float,
    )
    if aligned.shape != (len(names), target_wl.size):
        raise ValueError(f"aligned library shape {aligned.shape} != {(len(names), target_wl.size)}")
    if not np.isfinite(aligned).all():
        raise ValueError("aligned library contains non-finite values after resampling")
    return list(names), aligned
