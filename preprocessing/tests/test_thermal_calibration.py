# =============================================================
# OWNER: ANANTAHANARAYANAN
# =============================================================
"""
Reading the RIGHT calibration out of an MTL, and applying it.

A Landsat MTL describes both Level-1 and Level-2, reusing key names for
different values. A flat parser keeps the last one and applies Level-1 TOA
scaling to Level-2 data: 0.30 where the answer is 0.35.
"""

import numpy as np
import pytest

from thermal import calibration, mtl, reflectance

# The exact shape of the collision, taken from a real Landsat 9 MTL.
MTL_TEXT = """GROUP = LANDSAT_METADATA_FILE
  GROUP = LEVEL2_SURFACE_REFLECTANCE_PARAMETERS
    REFLECTANCE_MULT_BAND_5 = 2.75e-05
    REFLECTANCE_ADD_BAND_5 = -0.2
  END_GROUP = LEVEL2_SURFACE_REFLECTANCE_PARAMETERS
  GROUP = LEVEL2_SURFACE_TEMPERATURE_PARAMETERS
    TEMPERATURE_MULT_BAND_ST_B10 = 0.00341802
    TEMPERATURE_ADD_BAND_ST_B10 = 149.0
  END_GROUP = LEVEL2_SURFACE_TEMPERATURE_PARAMETERS
  GROUP = LEVEL1_RADIOMETRIC_RESCALING
    REFLECTANCE_MULT_BAND_5 = 2.0000E-05
    REFLECTANCE_ADD_BAND_5 = -0.100000
  END_GROUP = LEVEL1_RADIOMETRIC_RESCALING
END_GROUP = LANDSAT_METADATA_FILE
"""


@pytest.fixture
def meta(tmp_path):
    path = tmp_path / "MTL.txt"
    path.write_text(MTL_TEXT)
    return mtl.parse(str(path))


def test_groups_are_preserved(meta):
    """Both values survive parsing, in their own groups."""
    assert meta["LEVEL2_SURFACE_REFLECTANCE_PARAMETERS"][
        "REFLECTANCE_MULT_BAND_5"] == pytest.approx(2.75e-05)
    assert meta["LEVEL1_RADIOMETRIC_RESCALING"][
        "REFLECTANCE_MULT_BAND_5"] == pytest.approx(2.0e-05)


def test_reflectance_scaling_picks_level2(meta):
    """The whole point: Level-2 data must get Level-2 scaling."""
    scale, offset, from_mtl = calibration.reflectance_scaling(meta, 5)

    assert scale == pytest.approx(2.75e-05), "must not use the Level-1 value"
    assert offset == pytest.approx(-0.2)
    assert from_mtl is True


def test_wrong_group_would_change_the_answer(meta):
    """Quantifies why this matters, so nobody 'simplifies' it away."""
    correct = 20000 * 2.75e-05 - 0.2
    level1 = 20000 * 2.0e-05 - 0.1

    assert correct == pytest.approx(0.35, abs=1e-6)
    assert level1 == pytest.approx(0.30, abs=1e-6)
    assert abs(correct - level1) > 0.04


def test_thermal_scaling_reads_its_own_group(meta):
    scale, offset, from_mtl = calibration.thermal_scaling(meta, 0.0, 0.0)

    assert scale == pytest.approx(0.00341802)
    assert offset == pytest.approx(149.0)
    assert from_mtl is True


def test_falls_back_when_mtl_absent():
    """No MTL must degrade to documented constants, not crash."""
    scale, offset, from_mtl = calibration.reflectance_scaling({}, 5)

    assert (scale, offset) == (calibration.SR_SCALE, calibration.SR_OFFSET)
    assert from_mtl is False


def test_band_number_from_filename():
    assert calibration.band_number("LC09_..._SR_B5.TIF") == 5
    assert calibration.band_number("LC09_..._SR_B10.TIF") == 10
    assert calibration.band_number("LC09_..._ST_B10.TIF") is None


def test_reflectance_masks_fill_and_excludes_it_from_stats():
    """DN 0 is fill; it scales to -0.2, outside valid range, so it is caught."""
    dn = np.array([[0, 20000, 30000]], dtype=np.float32)
    out, stats = reflectance.prepare(dn, calibration.SR_SCALE,
                                     calibration.SR_OFFSET)

    assert out[0, 0] == 0.0, "fill written as the fill value"
    assert stats["min"] > 0.0, "fill must not reach the statistics"
    assert stats["invalid_fraction"] == pytest.approx(1 / 3, abs=0.01)
