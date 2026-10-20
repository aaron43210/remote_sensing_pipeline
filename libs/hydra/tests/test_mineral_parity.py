"""hydra's mineral features and teacher == the mineral owner's reference code."""

import os
import sys
from pathlib import Path

import numpy as np
import pytest

REF = Path(__file__).parent / "reference"
sys.path.insert(0, str(REF))

import continuum as ref_continuum            # noqa: E402
import features as ref_features              # noqa: E402
import mineral_analysis as ref_ma            # noqa: E402
import synth_reference                       # noqa: E402

from hydra import spec                                       # noqa: E402
from hydra.classical.library import aligned_library, load_library   # noqa: E402
from hydra.classical.mineral import MineralTeacher           # noqa: E402
from hydra.features.continuum import continuum_remove_many   # noqa: E402
from hydra.features.mineral import MineralFeatures           # noqa: E402

DATA_ROOT = Path(os.getenv("HYDRA_DATA_ROOT", Path(__file__).parents[4]))


@pytest.fixture(scope="module")
def spectra():
    wl = spec.emit_good_wavelengths()
    _, em = aligned_library(wl)
    s, _ = synth_reference.simulate(em, 300, np.random.default_rng(7))
    s = s.astype(np.float64)
    s[5, 10:20] = np.nan                   # partially missing pixel
    s[6, :] = np.nan                       # fully missing pixel
    return wl, em, s


def test_continuum_matches_reference(spectra):
    wl, _, s = spectra
    ours = continuum_remove_many(wl, s)
    theirs = ref_continuum.continuum_remove_many(wl, s)[1]
    np.testing.assert_allclose(ours, theirs, rtol=0, atol=1e-12, equal_nan=True)


def test_features_match_reference(spectra):
    wl, em, s = spectra
    ours = MineralFeatures().transform(s)
    theirs = ref_features.FeatureExtractor(wl, em, list(spec.MINERALS)).transform(s)
    np.testing.assert_allclose(ours, theirs, rtol=0, atol=1e-5, equal_nan=True)
    assert ours.shape[1] == spec.N_MINERAL_FEATURES == len(spec.mineral_feature_names())


def test_teacher_matches_mineral_analyzer(spectra):
    wl, _, s = spectra
    names, lib_wl, lib = load_library()
    ref = ref_ma.MineralAnalyzer(wl, names, lib_wl, lib,
                                 sam_max_angle=spec.SAM_MAX_ANGLE,
                                 sma_max_rmse=spec.SMA_MAX_RMSE).analyze(s)
    ours = MineralTeacher().label(s)
    ref_cls = np.where(ref.class_index == -1, spec.UNKNOWN_CLASS, ref.class_index)
    assert (ours["class_index"] == ref_cls).all()
    np.testing.assert_allclose(ours["abundances"], ref.abundances, atol=1e-8, equal_nan=True)
    np.testing.assert_allclose(ours["quality"], ref.quality["overall"], atol=1e-6, equal_nan=True)  # float32 angles


@pytest.mark.skipif(not (DATA_ROOT / "Mineral" / "training_sample.csv").exists(),
                    reason="training_sample.csv not available")
def test_regenerates_owner_training_sample():
    """Seed-0 synthesis + our features reproduce the owner's published rows."""
    import pandas as pd
    wl = spec.emit_good_wavelengths()
    _, em = aligned_library(wl)
    rng = np.random.default_rng(0)
    s, ab = synth_reference.simulate(em, 60_000, rng)
    pure = np.zeros((12 * 200, 12), np.float32)
    for m in range(12):
        pure[m * 200:(m + 1) * 200, m] = 1
    ps = pure @ em
    ps *= rng.uniform(0.6, 1.2, size=(ps.shape[0], 1))
    ps += rng.normal(0, 1, ps.shape) * rng.uniform(0.003, 0.015, size=(ps.shape[0], 1))
    np.clip(ps, 0.001, 1.5, out=ps)
    s = np.vstack([s, ps.astype(np.float32)])
    ab = np.vstack([ab, pure])
    order = rng.permutation(s.shape[0])
    x = MineralFeatures().transform(s[order][:300])
    csv = pd.read_csv(DATA_ROOT / "Mineral" / "training_sample.csv", nrows=300)
    np.testing.assert_allclose(x, csv[spec.mineral_feature_names()].values, atol=2e-5)
    np.testing.assert_allclose(ab[order][:300], csv[[f"abund_{m}" for m in spec.MINERALS]].values, atol=1e-6)


@pytest.mark.skipif(not (DATA_ROOT / "Mineral" / "dataset_sample.csv").exists(),
                    reason="dataset_sample.csv not available")
def test_real_scene_labels_are_sam_argmin():
    import pandas as pd
    d = pd.read_csv(DATA_ROOT / "Mineral" / "dataset_sample.csv")
    angles = d[[f"sam_{m}" for m in spec.MINERALS]].values
    assert (np.array(spec.MINERALS)[angles.argmin(axis=1)] == d["label_name"].values).all()
