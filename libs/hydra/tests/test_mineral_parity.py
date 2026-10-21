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
    theirs = ref_features.FeatureExtractor(wl, em, list(spec.MINERALS)).transform(s)   # reference layout (275)
    without_raw = np.r_[np.arange(spec.CR_SLICE.start, spec.CR_SLICE.stop),
                        np.arange(spec.DEPTH_SLICE.start, spec.N_MINERAL_FEATURES)]
    np.testing.assert_allclose(ours[:, without_raw], theirs, rtol=0, atol=1e-5, equal_nan=True)
    np.testing.assert_allclose(ours[:, spec.RAW_SLICE], s.astype(np.float32), equal_nan=True)   # raw-reflectance block
    assert ours.shape[1] == spec.N_MINERAL_FEATURES == len(spec.mineral_feature_names()) == 530


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


TRAINING_CSV = spec.dataset_file(DATA_ROOT, "mineral", "training_sample.csv")
REAL_CSV = spec.dataset_file(DATA_ROOT, "mineral", "dataset_sample.csv")


@pytest.mark.parametrize("csv_path", [TRAINING_CSV, REAL_CSV], ids=["training_sample", "dataset_sample"])
def test_features_reproduce_v2_csvs(csv_path):
    """Our 530 features from the CSV's own raw reflectance == the CSV's feature columns."""
    if not csv_path.exists():
        pytest.skip(f"{csv_path.name} not available")
    import pandas as pd
    d = pd.read_csv(csv_path)
    names = spec.mineral_feature_names()
    assert list(d.columns[:spec.N_MINERAL_FEATURES]) == names
    x = MineralFeatures().transform(d[names[spec.RAW_SLICE]].values)
    np.testing.assert_allclose(x, d[names].values, atol=2e-5)


@pytest.mark.skipif(not REAL_CSV.exists(), reason="dataset_sample.csv not available")
def test_teacher_reproduces_real_scene_labels():
    """The real-scene benchmark is labelled by SAM + SMA + quality: so is our teacher, exactly."""
    import pandas as pd
    d = pd.read_csv(REAL_CSV)
    refl = d[spec.mineral_feature_names()[spec.RAW_SLICE]].values
    lab = MineralTeacher().label(refl)
    assert (lab["class_index"] == d["label_index"].values).all()
    np.testing.assert_allclose(lab["abundances"], d[[f"abund_{m}" for m in spec.MINERALS]].values, atol=1e-5)
    np.testing.assert_allclose(lab["quality"], d["quality"].values, atol=1e-5)


@pytest.mark.skipif(not REAL_CSV.exists(), reason="dataset_sample.csv not available")
def test_real_scene_labels_are_sam_argmin():
    import pandas as pd
    d = pd.read_csv(REAL_CSV)
    angles = d[[f"sam_{m}" for m in spec.MINERALS]].values
    assert (np.array(spec.MINERALS)[angles.argmin(axis=1)] == d["label_name"].values).all()
