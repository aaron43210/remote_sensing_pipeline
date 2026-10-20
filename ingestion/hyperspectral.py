"""
EMIT branch: find the granule, stage it (once, shared by every job), crop and
orthorectify the user's bbox, write silver/{job}/raw.zarr.
"""

import hashlib
import logging
import os
import tempfile
import time
from pathlib import Path

import numpy as np
import redis
import zarr

from hs_common import storage
from hs_common.config import settings
from hs_common.kafka import NoData, Permanent
from hs_common.sources import emit
from hs_common.storage import keys

log = logging.getLogger(__name__)
LOCK_TTL_S = 30 * 60
FILL = -9999.0
_redis = redis.Redis.from_url(settings.redis_url, socket_timeout=10)


def _staged_granule(url, granule_id, workdir):
    """Local path to the granule; download from NASA at most once per granule platform-wide."""
    name = url.rsplit("/", 1)[-1]
    key = keys.emit_granule(granule_id, name)
    local = Path(workdir) / name
    if storage.exists(key):
        return storage.get_file(key, local)
    lock = f"lock:granule:{granule_id}"
    if _redis.set(lock, settings.service, nx=True, ex=LOCK_TTL_S):
        try:
            log.info("downloading %s", name)
            t0 = time.time()
            emit.download(url, local)
            storage.put_file(key, local)
            log.info("staged %s (%.0f MB, %.0fs)", name, local.stat().st_size / 1e6, time.time() - t0)
            return local
        finally:
            _redis.delete(lock)
    deadline = time.time() + LOCK_TTL_S          # another job is downloading it: wait
    while time.time() < deadline:
        time.sleep(5)
        if storage.exists(key):
            return storage.get_file(key, local)
        if not _redis.exists(lock):
            return _staged_granule(url, granule_id, workdir)
    raise TimeoutError(f"granule {granule_id} was not staged within {LOCK_TTL_S}s")


def write_raw_zarr(job_id, cube, wavelengths, good, georef, meta):
    """(H, W, B) float32 with NaN = no data -> silver/{job}/raw.zarr (fill -9999)."""
    key = keys.silver(job_id, "raw.zarr")
    data = np.where(np.isfinite(cube), cube, FILL).astype(np.float32)
    z = zarr.open_array(storage.zarr_store(key), mode="w", shape=data.shape, dtype="f4",
                        chunks=(256, 256, data.shape[2]))
    z[:] = data
    z.attrs.update({"wavelengths": [float(w) for w in wavelengths],
                    "good_wavelengths": [bool(g) for g in good],
                    "crs": georef["crs"], "transform": georef["transform"],
                    "surface_reflectance": True, "fill_value": FILL, **meta})
    return key


def fetch_nasa(req, workdir):
    granules = emit.search(req["bbox"], req.get("date_from"), req.get("date_to"))
    if not granules:
        raise NoData("no EMIT acquisition fully covers this area in the date range")
    g = granules[0]
    if not g["url"]:
        raise Permanent(f"granule {g['granule_id']} has no download URL")
    nc = _staged_granule(g["url"], g["granule_id"], workdir)
    cube, wl, good, georef = emit.ortho_crop(nc, req["bbox"])
    os.remove(nc)
    cloud = None
    mask_url = emit.mask_granule_for(g)
    if mask_url:
        mnc = _staged_granule(mask_url, g["granule_id"] + "_MASK", workdir)
        mcube, _, _, mgeo = emit.ortho_crop(mnc, req["bbox"], var="mask")
        if mcube.shape[:2] == cube.shape[:2]:
            cloud = emit.cloud_from_mask(mcube)
        os.remove(mnc)
    valid = np.isfinite(cube).all(axis=-1)
    if valid.mean() < 0.05:
        raise NoData("the selected EMIT scene has no valid pixels in this area")
    return cube, wl, good, georef, cloud, {"granule_id": g["granule_id"], "acquired_at": g["acquired_at"],
                                           "cloud_cover": g["cloud_cover"]}


FIXTURE_POOL = int(os.getenv("FIXTURE_POOL", "32"))


def fetch_fixture(req):
    """
    Physically based synthetic scene: for CI and load tests, labelled as such end to end.
    A pool of scenes is simulated once and cached in object storage (like a
    staged EMIT granule); each job only gets its own georeference.
    """
    from hydra import spec, synthetic
    h = int(hashlib.sha256(",".join(f"{v:.4f}" for v in req["bbox"]).encode()).hexdigest()[:8], 16)
    seed = h % FIXTURE_POOL
    key = keys.fixture(f"emit_scene_{seed:03d}.npy")
    if storage.exists(key):
        cube = storage.get_numpy(key)
    else:
        cube = synthetic.scene(seed, shape=(96, 96))[0].astype(np.float32)
        storage.put_numpy(key, cube)
    wl, good = spec.emit_bands()
    b = req["bbox"]
    h_, w_ = cube.shape[:2]
    georef = {"crs": "EPSG:4326", "transform": [(b[2] - b[0]) / w_, 0.0, b[0], 0.0, -(b[3] - b[1]) / h_, b[3]],
              "width": w_, "height": h_}
    return cube, wl, good, georef, None, {"granule_id": f"FIXTURE_{seed:03d}",
                                          "acquired_at": "2024-07-01T19:00:00Z", "cloud_cover": 0}


def run(req):
    """Returns the raw.hyperspectral message body (dict)."""
    with tempfile.TemporaryDirectory(prefix="emit_") as td:
        if req.get("source") == "fixture":
            cube, wl, good, georef, cloud, meta = fetch_fixture(req)
        else:
            cube, wl, good, georef, cloud, meta = fetch_nasa(req, td)
    job = req["job_id"]
    zkey = write_raw_zarr(job, cube, wl, good, georef, meta)
    cloud_key = storage.put_numpy(keys.silver(job, "cloud.npy"), cloud) if cloud is not None else None
    h, w = cube.shape[:2]
    return {"job_id": job, "satellite": "EMIT", "granule_id": meta["granule_id"],
            "acquired_at": meta["acquired_at"], "zarr_key": zkey, "wavelengths": [float(x) for x in wl],
            "good_bands": [bool(x) for x in good], "cloud_key": cloud_key,
            "georef": {**georef, "width": w, "height": h}, "bbox": req["bbox"], "tasks": req["tasks"],
            "source": req.get("source", "nasa")}
