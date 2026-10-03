# =============================================================
# OWNER: ANANTAHANARAYANAN
# =============================================================
"""
The Dask layer must change HOW the work runs, never the numbers.

Every step works along one pixel's spectrum and every chunk holds the full
spectrum, so the chunked result must equal the plain numpy steps applied to
the whole cube at once.
"""

import dask.array as da
import numpy as np
import pytest

import bands
import config
import lazy
import quality
from conftest import write_like_ingestion


def _stats_and_keep(cube, wavelengths, apply_dark=False):
    stats, _ = lazy.scene_stats(da.from_array(cube), wavelengths, apply_dark)
    keep, _ = bands.select(wavelengths)
    return stats, keep


@pytest.mark.parametrize("apply_dark", [False, True])
def test_chunked_equals_whole_cube(cube, wavelengths, monkeypatch, apply_dark):
    monkeypatch.setattr(config, "CHUNK_SIZE", 16)        # 40 px -> 16+16+8
    stats, keep = _stats_and_keep(cube, wavelengths, apply_dark)

    lazy_cube = da.from_array(cube).rechunk((16, 16, -1))
    chunked = lazy.build(lazy_cube, wavelengths, stats, keep).compute()
    whole = lazy.clean_chunk(cube, wavelengths, stats, keep)

    assert chunked.shape == (40, 40, len(bands.TARGET_BANDS_NM))
    np.testing.assert_allclose(chunked, whole, atol=1e-6)


def test_input_is_rechunked_to_full_spectrum(tmp_path, cube, wavelengths):
    """Ingestion writes (64, 64, bands); we need every chunk to hold all bands."""
    path = write_like_ingestion(tmp_path / "raw.zarr", cube, wavelengths)
    lazy_cube, attrs = lazy.open_cube(path)

    assert lazy_cube.chunks[2] == (len(wavelengths),)
    assert attrs["scene_id"] == "EMIT_test"


def test_output_is_clean(cube, wavelengths):
    stats, keep = _stats_and_keep(cube, wavelengths)
    out = lazy.clean_chunk(cube, wavelengths, stats, keep)
    assert np.isfinite(out).all() and out.min() >= 0.0 and out.max() <= 1.0


def test_gate_sees_fill_before_it_becomes_zero(cube):
    """
    After normalise(), fill is 0 and an empty pixel looks black. The gate
    must run on raw values -- an earlier version ran after and passed all.
    """
    empty = cube.copy()
    empty[:30, :, :] = -9999.0                           # 75% no data
    usable, report = quality.assess(empty)

    assert not usable
    assert report["nodata_fraction"] == pytest.approx(0.75)


def test_mostly_empty_scene_is_rejected(cube, wavelengths):
    empty = cube.copy()
    empty[:, :30, :] = np.nan
    with pytest.raises(lazy.SceneRejected):
        lazy.scene_stats(da.from_array(empty), wavelengths, apply_dark=False)


def test_partly_empty_scene_is_kept(cube, wavelengths):
    """A box at the swath edge is still worth processing."""
    partial = cube.copy()
    partial[:, :10, :] = -9999.0                         # 25% no data
    stats, report = lazy.scene_stats(da.from_array(partial), wavelengths, False)
    assert report["nodata_fraction"] < config.MAX_NODATA_FRACTION


def test_two_dimensional_input_is_refused(tmp_path):
    import zarr
    zarr.open(str(tmp_path / "flat.zarr"), mode="w", shape=(8, 8), dtype="f4")
    with pytest.raises(ValueError, match="rows, cols, bands"):
        lazy.open_cube(str(tmp_path / "flat.zarr"))
