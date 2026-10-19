"""Generates teacher targets (SAM classification + SMA abundance) for NN training.

Reuses the existing pipeline via MineralAnalyzer (spectral_library, wavelength_alignment,
continuum, sam, sma) -- no new spectral algorithms here. SAM/SMA run only here, offline;
the trained NN will not call them at inference.

Compatible with training_data_generator.py: feed it that module's "wavelengths" and
"reflectance" arrays (not the continuum-removed "spectrum", which MineralAnalyzer
derives for SAM on its own).
"""
import numpy as np

# The service pipeline modules live three levels up (services/mineral_analysis).
import sys
from pathlib import Path
_SERVICE_DIR = str(Path(__file__).resolve().parents[2])
if _SERVICE_DIR not in sys.path:
    sys.path.insert(0, _SERVICE_DIR)

from mineral_analysis import MineralAnalyzer

CHUNK_SIZE = 2000


def _select_minerals(names, spectra, wanted):
    """Pick `wanted` minerals from the library case-insensitively (keep display names)."""
    lower = {str(n).lower(): i for i, n in enumerate(names)}
    missing = sorted(w for w in wanted if str(w).lower() not in lower)
    if missing:
        raise ValueError(f"minerals not found in library: {missing}; available: {sorted(names)}")
    idx = [lower[str(w).lower()] for w in wanted]
    return list(wanted), np.asarray(spectra)[idx]


def build_analyzer(wavelengths, mineral_names, library_dir, *, spectral_library=None,
                   method="linear", max_gap=None, use_continuum_removal=True,
                   non_negative=True, sum_to_one=True, sam_max_angle=None, sma_max_rmse=None):
    """Build a MineralAnalyzer for these minerals, aligned to `wavelengths`.

    spectral_library: the loader module (injectable for testing); defaults to the
    project's spectral_library.py.
    """
    if spectral_library is None:
        import spectral_library
    lib_names, lib_wl, lib_spectra = spectral_library.load_library(library_dir)
    names, spectra = _select_minerals(list(lib_names), lib_spectra, mineral_names)
    return MineralAnalyzer(
        wavelengths, names, lib_wl, spectra, method=method, max_gap=max_gap,
        use_continuum_removal=use_continuum_removal, non_negative=non_negative,
        sum_to_one=sum_to_one, sam_max_angle=sam_max_angle, sma_max_rmse=sma_max_rmse,
    )


def generate_teacher_targets(reflectance, analyzer, chunk_size=CHUNK_SIZE):
    """Run the teacher (SAM + SMA) over a batch of reflectance spectra.

    reflectance: (n_samples, n_bands), matching analyzer's wavelengths. Invalid rows
    (e.g. NaN) come back as SAM "invalid" / NaN SMA values, not an error.
    Returns a dict of per-sample teacher targets (see module docstring for fields).
    """
    r = np.asarray(reflectance, dtype=float)
    if r.ndim != 2 or r.shape[0] == 0:
        raise ValueError("reflectance must be non-empty, shape (n_samples, n_bands)")
    n, k = r.shape[0], len(analyzer.names)

    class_index = np.empty(n, dtype=np.int32)
    mineral = np.empty(n, dtype=object)
    min_angle = np.empty(n)
    angles = np.empty((n, k))
    abundance = np.empty((n, k))
    rmse = np.empty(n)
    abundance_sum = np.empty(n)
    quality = np.full(n, np.nan)

    for start in range(0, n, chunk_size):
        sl = slice(start, start + chunk_size)
        res = analyzer.analyze(r[sl])
        class_index[sl] = res.class_index
        mineral[sl] = res.mineral
        min_angle[sl] = res.min_angle
        angles[sl] = res.angles
        abundance[sl] = res.abundances
        rmse[sl] = res.rmse
        abundance_sum[sl] = res.abundance_sum
        if res.quality is not None:
            quality[sl] = res.quality["overall"]

    return {
        "mineral_names": list(analyzer.names),
        "sam_class_index": class_index, "sam_mineral": mineral,
        "sam_min_angle": min_angle, "sam_angles": angles,
        "sma_abundance": abundance, "sma_rmse": rmse, "sma_abundance_sum": abundance_sum,
        "quality_overall": quality,
    }


def attach_teacher_targets(dataset, targets):
    """Merge training_data_generator's dataset with these teacher targets into one dict.

    Checks the two were built on the same wavelengths/minerals before merging.
    """
    if not np.array_equal(dataset["mineral_names"], targets["mineral_names"]):
        raise ValueError("dataset and targets use different mineral_names/order")
    if len(dataset["reflectance"]) != len(targets["sam_class_index"]):
        raise ValueError("dataset and targets have a different number of samples")
    merged = dict(dataset)
    merged.update({k: v for k, v in targets.items() if k != "mineral_names"})
    return merged


def save_teacher_targets(path, targets):
    np.savez(path, mineral_names=np.array(targets["mineral_names"], dtype=object),
             sam_class_index=targets["sam_class_index"], sam_mineral=np.array(targets["sam_mineral"], dtype=object),
             sam_min_angle=targets["sam_min_angle"], sam_angles=targets["sam_angles"],
             sma_abundance=targets["sma_abundance"], sma_rmse=targets["sma_rmse"],
             sma_abundance_sum=targets["sma_abundance_sum"], quality_overall=targets["quality_overall"])


def load_teacher_targets(path):
    with np.load(path, allow_pickle=True) as f:
        return {
            "mineral_names": f["mineral_names"].tolist(),
            "sam_class_index": f["sam_class_index"], "sam_mineral": f["sam_mineral"],
            "sam_min_angle": f["sam_min_angle"], "sam_angles": f["sam_angles"],
            "sma_abundance": f["sma_abundance"], "sma_rmse": f["sma_rmse"],
            "sma_abundance_sum": f["sma_abundance_sum"], "quality_overall": f["quality_overall"],
        }


def generate_teacher_dataset(config, bundle, path):
    """nn_main glue: run the SAM/SMA teacher over `bundle` and write a .npz in the
    SpectralDataset format (spectra/class_targets/abundances/sam_angles/group_ids/
    mineral_names). SAM class is the classification target, SMA abundance the
    regression target; where the teacher is invalid we fall back to the known
    synthetic truth so no sample is wasted.
    """
    names = list(bundle["mineral_names"])
    analyzer = build_analyzer(
        bundle["wavelengths"], names, config.library_dir,
        sam_max_angle=config.sam_max_angle, sma_max_rmse=config.sma_max_rmse,
    )
    targets = generate_teacher_targets(bundle["reflectance"], analyzer)

    n, m = bundle["reflectance"].shape[0], len(names)
    true_dom = np.asarray(bundle["dominant_class"], dtype=np.int64)
    true_ab = np.asarray(bundle["abundance"], dtype=np.float64)
    sam_cls = np.asarray(targets["sam_class_index"], dtype=np.int64)
    sma_ab = np.asarray(targets["sma_abundance"], dtype=np.float64)

    class_targets = np.where(sam_cls >= 0, sam_cls, true_dom).astype(np.int64)
    bad = ~np.isfinite(sma_ab).all(axis=1)
    abundances = sma_ab.copy()
    abundances[bad] = true_ab[bad]
    abundances = np.clip(abundances, 0.0, None)
    s = abundances.sum(axis=1, keepdims=True)
    s[s <= 0] = 1.0
    abundances = abundances / s

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez(
        path,
        spectra=np.asarray(bundle["spectrum"], dtype=np.float32),
        wavelengths=np.asarray(bundle["wavelengths"], dtype=np.float64),
        class_targets=class_targets,
        abundances=abundances.astype(np.float32),
        sam_angles=np.asarray(targets["sam_angles"], dtype=np.float32),
        group_ids=np.arange(n, dtype=np.int64),
        sample_ids=np.arange(n, dtype=np.int64),
        mineral_names=np.asarray(names),  # unicode array: loads with allow_pickle=False
    )
    return path
