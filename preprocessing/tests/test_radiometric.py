# =============================================================
# OWNER: ANANTAHANARAYANAN
# =============================================================
"""
Radiometry and the haze-removal decision.

The decision is the bug real EMIT data exposed: dark-object subtraction on
data that is already surface reflectance collapsed NDVI from +0.361 to
-0.041. The draft this module replaced applied it unconditionally.
"""

import numpy as np

import config
import product
import radiometric
import smoothing


# ── Scaling: decided once per scene ─────────────────────────────────────
def test_integer_reflectance_detected_and_divided(wavelengths):
    scaled = (np.random.rand(10, 10, len(wavelengths)) * 8000).astype(np.float32)
    scale = radiometric.detect_scale(scaled)
    assert scale == config.REFLECTANCE_SCALE
    assert radiometric.normalise(scaled, scale).max() <= 1.0


def test_float_reflectance_left_alone(cube):
    """EMIT is 0..1 already -- dividing again would zero the scene."""
    assert radiometric.detect_scale(cube) == 1.0
    np.testing.assert_allclose(radiometric.normalise(cube), cube, rtol=1e-6)


def test_fill_does_not_fool_scale_detection(cube):
    """-9999 must be ignored, or one empty pixel would decide the scale."""
    dirty = cube.copy()
    dirty[:5, :5, :] = -9999.0
    assert radiometric.detect_scale(dirty) == 1.0


def test_fill_nan_and_inf_become_zero(wavelengths):
    dirty = np.full((5, 5, len(wavelengths)), 0.3, dtype=np.float32)
    dirty[0, 0, :], dirty[1, 1, :], dirty[2, 2, :] = -9999.0, np.nan, np.inf
    out = radiometric.normalise(dirty)
    assert np.isfinite(out).all() and out.min() >= 0.0


# ── Haze removal: whether ───────────────────────────────────────────────
def test_emit_skips_haze_removal_even_without_a_flag():
    """Ingestion's raw.zarr carries no flag; satellite=EMIT must be enough."""
    assert not product.needs_dark_subtraction({}, "EMIT")


def test_store_flag_beats_satellite():
    assert product.needs_dark_subtraction({"surface_reflectance": False}, "EMIT")
    assert not product.needs_dark_subtraction({"surface_reflectance": True}, "X")


def test_unknown_raw_source_gets_haze_removal():
    assert product.needs_dark_subtraction({}, "AVIRIS-L1B")


def test_operator_override(monkeypatch):
    monkeypatch.setattr(config, "DARK_SUBTRACTION", "always")
    assert product.needs_dark_subtraction({}, "EMIT")
    monkeypatch.setattr(config, "DARK_SUBTRACTION", "never")
    assert not product.needs_dark_subtraction({}, "AVIRIS-L1B")


def test_haze_removal_on_corrected_data_damages_ndvi(cube, wavelengths):
    """The real-data failure in miniature -- why the decision exists."""
    red = int(np.argmin(np.abs(wavelengths - 660)))
    nir = int(np.argmin(np.abs(wavelengths - 860)))

    def ndvi(c):
        return float(((c[..., nir] - c[..., red])
                      / (c[..., nir] + c[..., red] + 1e-10)).mean())

    after = radiometric.subtract_dark(cube, radiometric.estimate_dark(cube))
    assert ndvi(cube) > 0.2
    assert ndvi(after) < ndvi(cube)


# ── Haze removal: how ───────────────────────────────────────────────────
def test_dark_is_one_value_per_band(cube):
    assert radiometric.estimate_dark(cube).shape == (cube.shape[2],)


def test_subtract_dark_neither_clips_nor_mutates(cube):
    before = cube.copy()
    out = radiometric.subtract_dark(cube, radiometric.estimate_dark(cube) + 0.2)
    assert out.min() < 0.0                   # clipping is the LAST step
    np.testing.assert_array_equal(cube, before)


def test_clip_after_smoothing_leaves_nothing_negative(cube):
    dark = radiometric.estimate_dark(cube)
    smoothed = smoothing.savgol(radiometric.subtract_dark(cube, dark))
    out = radiometric.clip_reflectance(smoothed)
    assert out.min() >= 0.0 and out.max() <= 1.0
