"""
Real EMIT pixels for training, cleaned EXACTLY as production cleans them.

    docker run --rm --user root -e PYTHONPATH=/app -v $PWD:/repo -v <granules>:/granules --entrypoint sh \
        hydranet/preprocessing:dev -c "pip install -q netCDF4==1.7.2 && \
        python /repo/training/harvest_emit.py /granules /repo/training/artifacts/data/real_scenes"

(the preprocessing image's own /app code, so the cleaning is the deployed one)

For every EMIT L2A granule (*.nc) in the input folder: take every STRIDE-th
swath pixel, run the preprocessing service's own Dask pipeline (lazy.scene_stats
+ lazy.build: scale, bad-band interpolation, Savitzky-Golay) and write
<granule>.npz with the cleaned 285-band spectra. datagen.py then keeps the bare
pixels, moves them to the 255-band grid and labels them with the classical
teacher, so the network trains on what it will actually see in production.
"""

import sys
import time
from pathlib import Path

import dask.array as da
import netCDF4
import numpy as np

import lazy
import product

STRIDE = 3          # every 3rd row and column of the swath (~180k pixels per granule)
FILL = -9999.0


def harvest(nc_path):
    with netCDF4.Dataset(nc_path) as ds:
        ds.set_auto_mask(False)
        refl = np.asarray(ds["reflectance"][::STRIDE, ::STRIDE, :], dtype=np.float32)
        sbp = ds["sensor_band_parameters"]
        wl = np.asarray(sbp["wavelengths"][:], dtype=float)
        good = np.asarray(sbp["good_wavelengths"][:]).astype(bool)
    cube = da.from_array(refl, chunks=(256, 256, -1))
    apply_dark = product.needs_dark_subtraction({"surface_reflectance": True}, "EMIT")
    stats, _ = lazy.scene_stats(cube, list(wl), apply_dark, list(good))
    clean = np.asarray(lazy.build(cube, list(wl), stats, list(range(cube.shape[2]))).compute(), np.float32)
    valid = (refl > FILL + 1).all(axis=-1) & (clean > 0).any(axis=-1)    # inside the swath, not empty
    return clean[valid], wl, int(stats["mask"].sum())


def main(src, dst):
    dst = Path(dst)
    dst.mkdir(parents=True, exist_ok=True)
    for nc in sorted(Path(src).glob("EMIT_L2A_RFL_*.nc")):
        out = dst / f"{nc.stem}.npz"
        if out.exists():
            print(f"{nc.stem}: already harvested", flush=True)
            continue
        t = time.time()
        pixels, wl, n_bad = harvest(nc)
        np.savez_compressed(out, reflectance=pixels.astype(np.float32), wavelengths=wl, granule_id=nc.stem)
        print(f"{nc.stem}: {len(pixels):,} pixels, {n_bad} bands interpolated, {time.time() - t:.0f}s", flush=True)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
