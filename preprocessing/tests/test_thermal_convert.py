# =============================================================
# OWNER: ANANTAHANARAYANAN
# =============================================================
"""
Landsat thermal conversion — the science, tested offline.

No network, no Earthdata credentials, no MinIO. These are the calculations
that decide what temperature every downstream anomaly map is built on, so
they are worth pinning precisely.
"""

import numpy as np
import pytest

from thermal import config, convert


def test_known_dn_converts_to_expected_celsius():
    """
    Pin the documented Landsat C2 L2 conversion.

    DN 44000 -> 44000 * 0.00341802 + 149.0 = 299.393 K -> 26.24 C,
    a plausible warm land surface.
    """
    celsius = convert.dn_to_celsius(
        np.array([[44000]]), config.ST_SCALE, config.ST_OFFSET
    )
    expected = 44000 * config.ST_SCALE + config.ST_OFFSET - 273.15
    np.testing.assert_allclose(celsius[0, 0], expected, rtol=1e-5)
    assert 20 < celsius[0, 0] < 35, "should be a plausible surface temperature"


def test_mtl_scale_overrides_the_fallback_constant():
    """A reprocessed scene ships its own calibration; it must win."""
    dn = np.array([[40000]])
    default = convert.dn_to_celsius(dn, config.ST_SCALE, config.ST_OFFSET)
    custom = convert.dn_to_celsius(dn, 0.00350000, 150.0)
    assert not np.isclose(default[0, 0], custom[0, 0])


def test_implausible_temperatures_are_masked():
    """Values outside the physical bounds become NaN, not silent outliers."""
    celsius = np.array([[-80.0, 25.0, 120.0]], dtype=np.float32)
    masked = convert.mask_implausible(celsius)

    assert np.isnan(masked[0, 0])       # below TEMP_MIN_C
    assert masked[0, 1] == 25.0         # valid, untouched
    assert np.isnan(masked[0, 2])       # above TEMP_MAX_C


def test_statistics_ignore_masked_pixels():
    """
    The scene mean is the thermal service's anomaly baseline. One unmasked
    fill value would drag it far enough to hide real hotspots.
    """
    celsius = np.array([[-9999.0, 20.0, 30.0]], dtype=np.float32)
    stats = convert.statistics(convert.mask_implausible(celsius))

    assert stats["temp_min_c"] == 20.0
    assert stats["temp_max_c"] == 30.0
    np.testing.assert_allclose(stats["temp_mean_c"], 25.0, rtol=1e-5)


def test_statistics_survive_a_fully_masked_scene():
    """A cloud-covered scene must return zeros, not raise."""
    stats = convert.statistics(np.full((4, 4), np.nan, dtype=np.float32))
    assert stats == {"temp_min_c": 0.0, "temp_max_c": 0.0,
                     "temp_mean_c": 0.0, "temp_std_c": 0.0}


def test_prepare_fills_invalid_but_excludes_it_from_stats():
    """
    Invalid pixels are written as config.FILL_VALUE (0.0, by consumer
    request) but must NEVER reach the statistics.

    That ordering is the whole safeguard. 0 C is a plausible temperature, so
    on a real scene where ~32% is empty UTM corner, letting fill into the
    mean gives 23.2 C against a true 34.1 C — and cog.write() declares it as
    nodata so a masked read still gets the right answer.
    """
    dn = np.array([[44000, 0, 44000]], dtype=np.float32)
    out, stats = convert.prepare(dn, config.ST_SCALE, config.ST_OFFSET)

    # DN 0 -> 149 K -> -124 C, below TEMP_MIN_C, so it is masked then filled.
    assert out[0, 1] == config.FILL_VALUE
    assert np.isfinite(out).all(), "no NaN survives into storage"
    assert stats["temp_min_c"] > config.TEMP_MIN_C, "fill must not reach stats"
    assert stats["invalid_fraction"] == pytest.approx(1 / 3, abs=0.01)


def test_external_invalid_mask_is_applied():
    """A cloud mask from qa.py must be honoured and kept out of the stats."""
    dn = np.full((1, 4), 44000, dtype=np.float32)
    cloud = np.array([[False, True, False, False]])

    out, stats = convert.prepare(dn, config.ST_SCALE, config.ST_OFFSET,
                                 invalid=cloud)
    assert out[0, 1] == config.FILL_VALUE
    assert stats["invalid_fraction"] == pytest.approx(0.25, abs=0.01)
