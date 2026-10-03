# =============================================================
# OWNER: ANANTAHANARAYANAN
# =============================================================
"""
Landsat entry points on small synthetic GeoTIFFs: cloud really is masked.

An earlier automated path dropped the QA band, sending cloud tops on as a
large cold anomaly while the manual script masked them. Both now go through
thermal/process.py, and these tests pin that down.
"""

import numpy as np
import pytest
import rasterio
from rasterio.transform import Affine

from thermal import config, process, qa

DN_30C = 45100            # 149 + 45100 * 0.00341802 K = 30.0 C
DN_CLOUD = 40000          # 12.6 C -- plausible, so only QA can catch it
DN_REFL = 20000           # 20000 * 2.75e-5 - 0.2 = 0.35 reflectance
MTL_TEXT = """GROUP = LEVEL2_SURFACE_REFLECTANCE_PARAMETERS
    REFLECTANCE_MULT_BAND_5 = 2.75e-05
    REFLECTANCE_ADD_BAND_5 = -0.2
END_GROUP = LEVEL2_SURFACE_REFLECTANCE_PARAMETERS
GROUP = LEVEL2_SURFACE_TEMPERATURE_PARAMETERS
    TEMPERATURE_MULT_BAND_ST_B10 = 0.00341802
    TEMPERATURE_ADD_BAND_ST_B10 = 149.0
END_GROUP = LEVEL2_SURFACE_TEMPERATURE_PARAMETERS
"""


def _tif(path, data):
    with rasterio.open(path, "w", driver="GTiff", width=4, height=4, count=1,
                       dtype="uint16", crs="EPSG:32614",
                       transform=Affine(30, 0, 500000, 0, -30, 5000000)) as dst:
        dst.write(data.astype(np.uint16), 1)
    return str(path)


@pytest.fixture
def scene(tmp_path):
    """4x4 scene: 2x2 cloud in one corner, one water pixel (to keep)."""
    bits = np.zeros((4, 4))
    bits[:2, :2], bits[3, 3] = qa.CLOUD, 1 << 7
    thermal = np.full((4, 4), DN_30C)
    thermal[:2, :2] = DN_CLOUD
    (tmp_path / "LC09_MTL.txt").write_text(MTL_TEXT)
    _tif(tmp_path / "LC09_QA_PIXEL.TIF", bits)
    _tif(tmp_path / "LC09_SR_B5.TIF", np.full((4, 4), DN_REFL))
    return _tif(tmp_path / "LC09_ST_B10.TIF", thermal), tmp_path


def test_cloud_becomes_fill_and_leaves_the_stats(scene):
    celsius, _, crs, tags = process.thermal_celsius(scene[0])

    assert tags["cloud_masked"] and tags["scaling_from_mtl"]
    assert np.all(celsius[:2, :2] == config.FILL_VALUE)
    assert celsius[3, 3] == pytest.approx(30.0, abs=0.01)       # water kept
    assert tags["temp_min_c"] == pytest.approx(30.0, abs=0.01)
    assert tags["invalid_fraction"] == pytest.approx(0.25)
    assert str(crs) == "EPSG:32614"


def test_without_qa_it_says_so(scene):
    _, _, _, tags = process.thermal_celsius(scene[0], qa_path=None)
    assert tags["cloud_masked"] is False
    assert tags["temp_min_c"] == pytest.approx(12.6, abs=0.05)  # cloud leaks


def test_reflectance_band_uses_level2_scaling_and_mask(scene):
    values, _, _, tags = process.surface_reflectance(
        str(scene[1] / "LC09_SR_B5.TIF"))

    assert tags["band"] == "SR_B5" and tags["scaling_from_mtl"]
    assert values[3, 3] == pytest.approx(0.35, abs=1e-4)
    assert np.all(values[:2, :2] == config.FILL_VALUE)


def test_geotiff_declares_nodata(scene, tmp_path):
    celsius, transform, crs, tags = process.thermal_celsius(scene[0])
    path = process.write_geotiff(str(tmp_path / "lst.tif"), celsius,
                                 transform, crs, tags)
    with rasterio.open(path) as src:
        assert src.nodata == config.FILL_VALUE
