"""
Preprocessing service (hyperspectral): raw.hyperspectral -> preprocessed.hyperspectral.

The science is the existing Dask pipeline (lazy.py, radiometric.py,
interpolation.py, smoothing.py); this file wires it to the platform:

    silver/{job}/raw.zarr  ->  silver/{job}/reflectance.zarr   all bands, cleaned (HydraNet input)
                           ->  silver/{job}/multiband.zarr     the 16 target bands (quick-look indices)

Run `python service.py thermal` for the Landsat worker (thermal_service.py).
"""

import logging
import sys
import time

import numpy as np
import zarr

import bands
import lazy
import product
from hs_common import logs, status, storage
from hs_common.config import settings
from hs_common.kafka import Permanent, Worker, publish
from hs_common.messages import PreprocessedHyperspectral, RawHyperspectral
from hs_common.storage import keys

log = logging.getLogger("preprocessing")


def _write(key, arr, attrs):
    z = zarr.open_array(storage.zarr_store(key), mode="w", shape=arr.shape, dtype="f4",
                        chunks=(256, 256, arr.shape[2]))
    z[:] = arr
    z.attrs.update(attrs)
    return key


def handle(topic, raw):
    msg = RawHyperspectral(**raw)
    job = msg.job_id
    status.report(job, "preprocess", "RUNNING", "cleaning the spectra")
    t0 = time.time()
    cube, attrs = lazy.open_cube(storage.zarr_store(msg.zarr_key))
    wl = list(attrs.get("wavelengths") or msg.wavelengths)
    if len(wl) != cube.shape[2]:
        raise Permanent(f"{len(wl)} wavelengths for {cube.shape[2]} bands")
    good = attrs.get("good_wavelengths") or msg.good_bands
    apply_dark = product.needs_dark_subtraction(attrs, msg.satellite)
    try:
        stats, report = lazy.scene_stats(cube, wl, apply_dark, good)
    except lazy.SceneRejected as e:
        raise Permanent(f"scene rejected by the quality gate: {e}")

    full = np.asarray(lazy.build(cube, wl, stats, list(range(cube.shape[2]))).compute(), dtype=np.float32)
    keep, actual = bands.select(wl)
    common = {"job_id": job, "crs": msg.georef.crs, "transform": msg.georef.transform,
              "units": "surface_reflectance", "nodata": "all bands 0", "granule_id": msg.granule_id,
              "source": msg.source}
    rkey = _write(keys.silver(job, "reflectance.zarr"), full,
                  {**common, "wavelengths": [float(w) for w in wl],
                   "bad_band_mask": [bool(b) for b in stats["mask"]]})
    mkey = _write(keys.silver(job, "multiband.zarr"), full[:, :, keep],
                  {**common, "band_names": bands.column_names(), "wavelengths": actual,
                   "interpolated_bands": bands.interpolated(keep, stats["mask"])})
    report.update({"seconds": round(time.time() - t0, 2), "dark_subtraction": bool(apply_dark),
                   "bad_bands": int(np.sum(stats["mask"])), "scale": float(stats["scale"])})
    publish("preprocessed.hyperspectral", PreprocessedHyperspectral(
        job_id=job, satellite=msg.satellite, granule_id=msg.granule_id, acquired_at=msg.acquired_at,
        reflectance_key=rkey, multiband_key=mkey, wavelengths=[float(w) for w in wl], cloud_key=msg.cloud_key,
        georef=msg.georef, bbox=msg.bbox, tasks=msg.tasks, quality=report, source=msg.source))
    status.report(job, "inference", "RUNNING", "running HydraNet")


class HyperspectralWorker(Worker):
    def on_give_up(self, raw, err):
        status.report(raw.get("job_id", ""), "preprocess", "ERROR", f"preprocessing failed: {err}")


def main():
    logs.setup()
    if len(sys.argv) > 1 and sys.argv[1] == "thermal":
        import thermal_service
        thermal_service.main()
        return
    storage.ensure_bucket()
    worker = HyperspectralWorker("preprocessing", ["raw.hyperspectral"], _guarded(handle))
    worker.run()


def _guarded(fn):
    """Report permanent input errors to the job before they go to the DLQ."""
    def wrapper(topic, raw):
        try:
            fn(topic, raw)
        except Permanent as e:
            status.report(raw.get("job_id", ""), "preprocess", "ERROR", str(e))
            raise
    return wrapper


if __name__ == "__main__":
    main()
