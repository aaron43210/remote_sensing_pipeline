# =============================================================
# OWNER: ANANTAHANARAYANAN
# =============================================================
"""
One scene end to end -- the file to read to understand this stage.

    input Zarr -> quality gate -> scene stats (once) -> Dask: per chunk
    normalise, [haze], interpolate bad bands, smooth, keep target bands, clip
    -> preprocessed.zarr (+ multiband.npy) -> message

Storage is passed in, so the same code runs on a local folder in the tests,
on MinIO in docker-compose, and on S3 on AWS.
"""

import logging

import numpy as np
import zarr

import bands
import lazy
import message
import product

logger = logging.getLogger(__name__)


def prepare(in_store, source):
    """
    Plan the work. Reads only a 1-in-16 sample; computes no output yet.

    Returns:
        (multiband, info) -- a lazy (rows, cols, n_bands) array and what
        was done.
    """
    cube, attrs = lazy.open_cube(in_store)
    # The store's own axis wins over the message: it describes these pixels.
    wavelengths = list(attrs.get("wavelengths") or source.get("wavelengths")
                       or [])
    if len(wavelengths) != cube.shape[2]:
        raise ValueError(f"{len(wavelengths)} wavelengths for "
                         f"{cube.shape[2]} bands -- cannot tell bands apart")

    apply_dark = product.needs_dark_subtraction(attrs, source.get("satellite"))
    keep, actual = bands.select(wavelengths)
    stats, report = lazy.scene_stats(cube, wavelengths, apply_dark)

    info = {"wavelengths": actual, "dark_subtraction": apply_dark,
            "interpolated_bands": bands.interpolated(keep, stats["mask"]),
            "quality": report}
    logger.info("%s: %s cube, haze removal %s, interpolated targets %s",
                source.get("scene_id"), cube.shape,
                "ON" if apply_dark else "off", info["interpolated_bands"])
    return lazy.build(cube, wavelengths, stats, keep), info


def write(multiband, out_store, info, source):
    """Compute chunk by chunk straight into the output Zarr."""
    multiband.to_zarr(out_store, overwrite=True)
    z = zarr.open_array(out_store, mode="r+")
    # Self-describing, so the store is usable without the Kafka message.
    z.attrs.update({
        "scene_id": source["scene_id"], "bbox": source.get("bbox"),
        "band_names": bands.column_names(), "wavelengths": info["wavelengths"],
        "interpolated_bands": info["interpolated_bands"],
        "units": "surface_reflectance", "nodata": "all bands 0",
    })
    return z


def run_scene(source, in_store, out_store, save_npy=None):
    """
    Preprocess one scene. Returns the message for 'preprocessed-multiband'.

    save_npy: optional callable(array) -> key, for consumers that read a
    single .npy file (ml_inference today).
    """
    multiband, info = prepare(in_store, source)
    z = write(multiband, out_store, info, source)

    data_path = save_npy(np.asarray(z[:])) if save_npy else None
    return message.build(source, info, z.shape, data_path)
