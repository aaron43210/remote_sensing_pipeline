"""ortho_crop on a file with EMIT L2A's exact layout (groups, GLT, geotransform)."""

import numpy as np
import pytest

netCDF4 = pytest.importorskip("netCDF4")
from hs_common.sources import emit   # noqa: E402


def make_granule(path, rows=40, cols=30, bands=5):
    ds = netCDF4.Dataset(path, "w")
    ds.createDimension("downtrack", rows); ds.createDimension("crosstrack", cols); ds.createDimension("bands", bands)
    ds.createDimension("ortho_y", 50); ds.createDimension("ortho_x", 60)
    # swath value encodes its own (row, col) so we can check where each pixel lands
    refl = ds.createVariable("reflectance", "f4", ("downtrack", "crosstrack", "bands"))
    r, c = np.mgrid[:rows, :cols]
    refl[:] = np.repeat((r * 1000 + c)[..., None], bands, axis=2).astype(np.float32)
    refl[0, 0, :] = -9999.0
    sbp = ds.createGroup("sensor_band_parameters")
    sbp.createVariable("wavelengths", "f4", ("bands",))[:] = np.linspace(400, 2400, bands)
    sbp.createVariable("good_wavelengths", "u1", ("bands",))[:] = ([1, 1, 0] + [1] * bands)[:bands]
    loc = ds.createGroup("location")
    gx = loc.createVariable("glt_x", "i4", ("ortho_y", "ortho_x"))
    gy = loc.createVariable("glt_y", "i4", ("ortho_y", "ortho_x"))
    oy, ox = np.mgrid[:50, :60]
    inside = (oy < rows) & (ox < cols)
    gx[:] = np.where(inside, ox + 1, 0)          # 1-based, 0 = no swath pixel
    gy[:] = np.where(inside, oy + 1, 0)
    ds.setncattr("geotransform", np.array([-120.0, 0.001, 0.0, 37.0, 0.0, -0.001]))
    ds.close()


def test_crop_places_swath_pixels_on_the_grid(tmp_path):
    p = tmp_path / "g.nc"
    make_granule(p)
    bbox = [-119.995, 36.975, -119.985, 36.990]          # ortho cols 5..15, rows 10..25
    cube, wl, good, georef = emit.ortho_crop(p, bbox)
    assert georef["crs"] == "EPSG:4326"
    a, b, c0, d, e, f0 = georef["transform"]
    assert a == pytest.approx(0.001) and e == pytest.approx(-0.001)
    col0, row0 = round((c0 + 120.0) / 0.001), round((37.0 - f0) / 0.001)
    # pixel (i, j) of the crop must hold swath pixel (row0 + i, col0 + j)
    i, j = 3, 4
    assert cube[i, j, 0] == (row0 + i) * 1000 + (col0 + j)
    assert list(good) == [True, True, False, True, True]
    assert wl.shape == (5,)


def test_fill_value_becomes_nan(tmp_path):
    p = tmp_path / "g.nc"
    make_granule(p)
    cube, *_ = emit.ortho_crop(p, [-120.0, 36.990, -119.990, 37.0])
    assert np.isnan(cube[0, 0]).all()


def test_bbox_outside_granule_raises(tmp_path):
    p = tmp_path / "g.nc"
    make_granule(p)
    with pytest.raises(ValueError):
        emit.ortho_crop(p, [-100.0, 10.0, -99.9, 10.1])


V003 = ["Cloud Flag", "Cirrus Flag", "Water Flag", "Dilated Cloud Flag", "SpecTf-Cloud Probability",
        "SpecTf-Cloud Flag", "SpecTf-Buffer Distance"]


def test_cloud_mask_v003_uses_flags_by_name():
    # a clear scene: every flag 0, but the SpecTf probability and buffer distance are > 0
    # everywhere (as on EMIT_L2A_MASK_003_20230822T192555). Must NOT read as cloud.
    m = np.zeros((4, 5, 7), np.float32)
    m[..., 4] = 0.02
    m[..., 6] = 6.3
    assert not emit.cloud_from_mask(m, V003).any()
    m[1, 2, 5] = 1.0                                     # SpecTf cloud flag
    m[3, 0, 3] = 1.0                                     # dilated cloud flag
    m[0, 4, 2] = 1.0                                     # water is not cloud
    got = emit.cloud_from_mask(m, V003)
    assert got[1, 2] and got[3, 0] and not got[0, 4] and got.sum() == 2


def test_cloud_mask_legacy_layout_without_names():
    m = np.zeros((2, 2, 7), np.float32)
    m[..., 5] = 0.3                                      # AOD, continuous
    m[0, 0, 4] = 1.0                                     # v001/002 dilated cloud
    got = emit.cloud_from_mask(m)
    assert got[0, 0] and got.sum() == 1


def test_mask_band_names_are_read(tmp_path):
    p = tmp_path / "m.nc"
    make_granule(p, bands=7)
    with netCDF4.Dataset(p, "a") as ds:
        ds.createVariable("mask", "f4", ("downtrack", "crosstrack", "bands"))[:] = 0.0
        v = ds["sensor_band_parameters"].createVariable("mask_bands", str, ("bands",))
        for i, n in enumerate(V003):
            v[i] = n
    cube, names, _, _ = emit.ortho_crop(p, [-119.995, 36.975, -119.985, 36.990], var="mask")
    assert list(names) == V003 and cube.shape[2] == 7


def test_cmr_outage_is_told_apart_from_bad_requests():
    import requests

    def http(code):
        r = requests.Response(); r.status_code = code
        return requests.HTTPError(response=r)
    assert emit.cmr_unavailable(requests.ReadTimeout())
    assert emit.cmr_unavailable(requests.ConnectionError())
    assert emit.cmr_unavailable(http(504)) and emit.cmr_unavailable(http(500))
    assert not emit.cmr_unavailable(http(400)) and not emit.cmr_unavailable(ValueError())
