# =============================================================
# OWNER: ANANTAHANARAYANAN
# =============================================================
"""
The Dask layer: raw cube in, clean target bands out, one chunk at a time.

    open_cube()    lazy view of the input Zarr, re-chunked (256, 256, ALL bands)
    scene_stats()  scale, haze and bad bands -- measured ONCE, from a sample
    build()        map_blocks(clean_chunk) -> lazy (rows, cols, n_bands)

Nothing is computed until the caller writes the result.

Why map_blocks is exact: every step works along ONE pixel's spectrum, and
each chunk holds the full spectrum. So a chunked run gives the same numbers
as running the plain numpy steps on the whole cube -- tests check this.
"""

import functools

import dask.array as da
import numpy as np
import zarr

import bad_bands
import config
import interpolation
import quality
import radiometric
import smoothing


class SceneRejected(Exception):
    """The scene is too empty to preprocess. Do not publish it."""


def open_cube(store):
    """Open a plain (rows, cols, bands) Zarr array. Returns (cube, attrs)."""
    z = zarr.open_array(store, mode="r")
    if z.ndim != 3:
        raise ValueError(f"Expected (rows, cols, bands), got shape {z.shape}")
    cube = da.from_zarr(z).rechunk((config.CHUNK_SIZE, config.CHUNK_SIZE, -1))
    return cube, dict(z.attrs)


def scene_stats(cube, wavelengths, apply_dark):
    """
    Everything that must be the SAME for every chunk, from every Nth pixel.

    Per-chunk estimates would differ between, say, a water chunk and a field
    chunk and leave a visible seam where they meet.
    """
    step = config.STATS_STRIDE
    sample = np.asarray(cube[::step, ::step, :].compute())

    usable, report = quality.assess(sample)
    if not usable:
        raise SceneRejected(report)

    scale = radiometric.detect_scale(sample)
    sample = radiometric.normalise(sample, scale)
    stats = {
        "scale": scale,
        "dark": radiometric.estimate_dark(sample) if apply_dark else None,
        "mask": bad_bands.snr_reject(sample, bad_bands.build_mask(wavelengths)),
    }
    return stats, report


def clean_chunk(chunk, wavelengths, stats, keep):
    """The numpy steps, in order, on one chunk. Returns only bands `keep`."""
    out = radiometric.normalise(chunk, stats["scale"])
    if stats["dark"] is not None:
        out = radiometric.subtract_dark(out, stats["dark"])
    out = interpolation.interpolate(out, wavelengths, stats["mask"])
    out = smoothing.savgol(out)
    # Clip LAST: smoothing can ring below zero next to dark pixels.
    return radiometric.clip_reflectance(out[:, :, keep])


def build(cube, wavelengths, stats, keep):
    """Lazy (rows, cols, len(keep)) float32 array, chunked like the input."""
    fn = functools.partial(clean_chunk, wavelengths=np.asarray(wavelengths),
                           stats=stats, keep=list(keep))
    # meta= tells Dask the output type up front; otherwise it probes fn with an
    # empty dummy array, which the band indexing rightly rejects.
    return cube.map_blocks(fn, dtype=np.float32,
                           meta=np.empty((0, 0, 0), dtype=np.float32),
                           chunks=(cube.chunks[0], cube.chunks[1], (len(keep),)))
