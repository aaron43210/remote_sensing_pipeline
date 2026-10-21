import json
from pathlib import Path

import numpy as np
import pytest

from hydra import spec
from hydra.features import agri

MODEL = Path(__file__).parents[3] / "training" / "artifacts" / "models" / "v1" / "hydra.onnx"   # the deployed version


def test_agri_interpolates_exact_wavelengths():
    wl = np.arange(380.0, 2500.0, 7.4)
    spectra = np.tile(wl / 1000.0, (3, 1))                    # linear spectrum: interpolation is exact
    x = agri.select(spectra, wl)
    np.testing.assert_allclose(x[0], np.array(spec.AGRI_BANDS_NM) / 1000.0, atol=1e-6)


def test_agri_refuses_missing_coverage():
    with pytest.raises(ValueError):
        agri.select(np.zeros((1, 3)), [400.0, 500.0, 600.0])


@pytest.mark.skipif(not MODEL.exists(), reason="trained model not present")
def test_runtime_refuses_other_feature_spec(tmp_path, monkeypatch):
    from hydra import runtime
    rt = runtime.HydraRuntime(MODEL)
    assert rt.metadata["feature_spec_hash"] == spec.feature_spec_hash()
    monkeypatch.setattr(runtime.spec, "feature_spec_hash", lambda: "different")
    with pytest.raises(runtime.SpecMismatch):
        runtime.HydraRuntime(MODEL)


@pytest.mark.skipif(not MODEL.exists(), reason="trained model not present")
def test_runtime_shapes_and_ranges():
    from hydra.runtime import HydraRuntime
    rt = HydraRuntime(MODEL)
    rng = np.random.default_rng(0)
    p = rt.predict("agri", rng.uniform(0.01, 0.6, (50, len(spec.AGRI_BANDS_NM))))
    assert p["agri_params"].shape == (50, 6)
    for j, name in enumerate(spec.AGRI_PARAMS):
        lo, hi = spec.AGRI_RANGES[name]
        assert (p["agri_params"][:, j] >= lo - 1e-6).all() and (p["agri_params"][:, j] <= hi + 1e-6).all()
    m = rt.predict("mineral", rng.uniform(0, 1, (20, spec.N_MINERAL_FEATURES)))
    np.testing.assert_allclose(m["mineral_abundance"].sum(1), 1.0, atol=1e-5)
    np.testing.assert_allclose(m["mineral_class_probs"].sum(1), 1.0, atol=1e-5)


def test_agreement_uses_range_normalised_error_on_uniform_areas():
    # a near-uniform field: the reference barely varies, the network is off by ~0.5 ug/cm2
    from hydra import maps
    rng = np.random.default_rng(0)
    ref = 40.0 + rng.normal(0, 0.05, (30, 30)).astype(np.float32)
    nn = ref + rng.normal(0, 0.5, ref.shape).astype(np.float32)
    a = maps.agreement({"agri_Cab": nn}, {"agri_Cab": ref})["agri_Cab"]
    assert a["r2"] < 0                       # R² collapses although the error is tiny ...
    assert a["nmae"] < 0.01                  # ... nMAE (MAE / 110 ug/cm2 range) stays honest
    assert maps.value_range("agri_Cw") == pytest.approx(0.07) and maps.value_range("thermal_lst") is None
