# =============================================================
# OWNER: ANANTAHANARAYANAN
# =============================================================
"""
The contract with ingestion (in) and ml_inference (out), end to end.

Runs pipeline.run_scene() on a Zarr laid out exactly like ingestion writes
it, then checks what ml_inference will read: the target bands, fixed order,
0..1, no NaN, data_path pointing at a matching .npy.
"""

import numpy as np
import pytest
import zarr

import bands
import config
import message
import pipeline
from conftest import write_like_ingestion

N = len(bands.TARGET_BANDS_NM)


@pytest.fixture
def result(tmp_path, raw_store, source):
    saved = {}

    def save_npy(array):
        saved["array"] = array
        return "EMIT_test/multiband.npy"

    out_store = str(tmp_path / "preprocessed.zarr")
    msg = pipeline.run_scene(source, raw_store, out_store, save_npy)
    return msg, zarr.open_array(out_store, mode="r"), saved["array"]


def test_output_is_what_the_network_reads(result):
    msg, z, npy = result
    assert z.shape == (40, 40, N) and msg["shape"] == [40, 40, N]
    assert msg["n_bands"] == N and msg["data_path"] == "EMIT_test/multiband.npy"
    assert msg["band_names"] == bands.column_names()
    np.testing.assert_array_equal(npy, z[:])
    assert np.isfinite(npy).all() and npy.min() >= 0.0 and npy.max() <= 1.0


def test_fill_pixel_is_zero_in_every_band(result):
    """Documented no-data convention: all bands 0."""
    _, z, _ = result
    assert not z[0, 0, :].any()


def test_bands_come_out_in_target_order(tmp_path, wavelengths, source):
    """Each band's value encodes its wavelength, so the order is visible."""
    noise = np.random.default_rng(0).normal(0, 1e-4, (8, 8, len(wavelengths)))
    ramp = wavelengths / 3000.0 + noise          # noise keeps the SNR check real
    path = write_like_ingestion(tmp_path / "ramp.zarr", ramp.astype("f4"),
                                wavelengths)
    msg = pipeline.run_scene(source, path, str(tmp_path / "out.zarr"))

    got_nm = zarr.open_array(str(tmp_path / "out.zarr"))[4, 4, :] * 3000.0
    np.testing.assert_allclose(got_nm, bands.TARGET_BANDS_NM, atol=8.0)
    assert msg["band_names"][0] == "b450" and msg["data_path"] is None


def test_every_target_band_is_measured_not_interpolated():
    for nm in bands.TARGET_BANDS_NM:
        assert config.WAVELENGTH_MIN <= nm <= config.WAVELENGTH_MAX, nm
        for lo, hi in config.WATER_WINDOWS:
            assert not lo <= nm <= hi, f"{nm} nm is inside water window {lo}-{hi}"


def test_clean_scene_interpolates_no_target_and_skips_haze_removal(result):
    msg, _, _ = result
    assert msg["interpolated_bands"] == []
    assert msg["dark_subtraction"] is False                  # EMIT is L2A
    assert msg["scene_id"] == "EMIT_test" and msg["bbox"] is not None


def test_wavelength_count_mismatch_is_refused(tmp_path, cube, wavelengths, source):
    path = write_like_ingestion(tmp_path / "bad.zarr", cube, wavelengths[:-1])
    with pytest.raises(ValueError, match="wavelengths"):
        pipeline.run_scene(source, path, str(tmp_path / "out.zarr"))


def test_target_outside_sensor_range_is_refused():
    with pytest.raises(ValueError, match="No band within"):
        bands.select(np.linspace(400, 1000, 80))             # no SWIR


def test_decode_accepts_msgpack_and_json():
    msgpack = pytest.importorskip("msgpack")
    body = {"scene_id": "S", "bbox": [1, 2, 3, 4]}
    assert message.decode(msgpack.packb(body)) == body
    assert message.decode(b'{"scene_id": "S", "bbox": [1, 2, 3, 4]}') == body
    assert message.decode(message.encode(body)) == body
