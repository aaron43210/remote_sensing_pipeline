"""
Physically based synthetic EMIT scenes, for map-level verification, CI and load tests.

A scene is a (H, W, 285) reflectance cube on the real EMIT band grid with
spatially coherent land cover: crop fields (PROSAIL canopies whose parameters
vary smoothly inside each field), bare ground (USGS mineral mixtures with
smoothly varying abundances), water and roads, plus sensor noise and the
real EMIT bad-band behaviour (water-vapour windows near zero).

These are TEST FIXTURES: every output that comes from one is labelled
source="fixture" all the way to the UI. They are never presented as imagery.
Needs the `prosail` package (training extras).
"""

import numpy as np
from scipy import ndimage

from . import spec
from .classical import agri_lut
from .classical.library import aligned_library

_PROSAIL_GRID = np.arange(400, 2501, dtype=float)


def _smooth(rng, shape, sigma):
    f = ndimage.gaussian_filter(rng.normal(size=shape), sigma)
    return (f - f.min()) / (np.ptp(f) + 1e-12)


def scene(seed=0, shape=(96, 96), bbox=(-119.85, 36.70, -119.75, 36.78)):
    """
    Returns (cube float32 (H, W, 285), wavelengths (285,), good (285,), georef, landcover).
    landcover: 0 field, 1 bare, 2 water, 3 road.
    """
    rng = np.random.default_rng(seed)
    wl, good = spec.emit_bands()
    h, w = shape
    # land cover: Voronoi-like parcels
    n_parcels = rng.integers(6, 14)
    cy, cx = rng.integers(0, h, n_parcels), rng.integers(0, w, n_parcels)
    yy, xx = np.mgrid[:h, :w]
    parcel = np.argmin((yy[..., None] - cy) ** 2 + (xx[..., None] - cx) ** 2, axis=-1)
    kind = rng.choice([0, 0, 0, 1, 1, 2], size=n_parcels)
    land = kind[parcel]
    road = (np.abs(yy - h // 2 + 3 * np.sin(xx / 9)) < 1.0) | (np.abs(xx - w // 3) < 1.0)
    land[road] = 3

    cube = np.zeros((h, w, wl.size), np.float32)
    # fields: per-parcel base parameters + smooth within-field variation
    lo = np.array([spec.AGRI_RANGES[p][0] for p in spec.AGRI_PARAMS])
    hi = np.array([spec.AGRI_RANGES[p][1] for p in spec.AGRI_PARAMS])
    var = np.stack([_smooth(rng, shape, 6) for _ in spec.AGRI_PARAMS], -1)
    base = rng.uniform(0.2, 0.8, (n_parcels, len(lo)))
    params = lo + np.clip(base[parcel] + 0.25 * (var - 0.5), 0.02, 0.98) * (hi - lo)
    sel = land == 0
    if sel.any():
        canopy = agri_lut.simulate(params[sel], rows=np.arange(0, 2101))
        cube[sel] = np.stack([np.interp(wl, _PROSAIL_GRID, s) for s in canopy])
    # bare ground: smooth abundance fields over 3-4 minerals per parcel
    _, em = aligned_library(wl[(wl >= 350) & (wl <= 2550)])
    sel = land == 1
    if sel.any():
        ab = np.zeros((h, w, len(spec.MINERALS)))
        for p in np.unique(parcel[sel]):
            members = rng.choice(len(spec.MINERALS), size=rng.integers(1, 4), replace=False)
            for m in members:
                ab[..., m] += (parcel == p) * (0.2 + _smooth(rng, shape, 5))
        ab = ab[sel] / ab[sel].sum(1, keepdims=True)
        bright = 0.6 + 0.6 * _smooth(rng, shape, 8)[sel][:, None]
        cube[sel] = (ab @ em) * bright
    # water and roads
    sel = land == 2
    x = (wl - 380.0) / 2120.0
    cube[sel] = (0.05 * np.exp(-x * 10) + 0.005)[None, :] * rng.uniform(0.8, 1.2, (sel.sum(), 1))
    sel = land == 3
    cube[sel] = (0.08 + 0.05 * x)[None, :] * rng.uniform(0.9, 1.1, (sel.sum(), 1))

    cube += rng.normal(0, 0.004, cube.shape).astype(np.float32)
    cube[..., ~good] = rng.uniform(0, 0.02, (h, w, (~good).sum()))   # water-vapour windows
    cube = np.clip(cube, 0.0, 1.0)

    dx = (bbox[2] - bbox[0]) / w
    dy = (bbox[3] - bbox[1]) / h
    georef = {"crs": "EPSG:4326", "transform": [dx, 0.0, bbox[0], 0.0, -dy, bbox[3]],
              "width": w, "height": h}
    return cube, wl, good, georef, land


def synthetic_lst(rng, shape=(128, 128)):
    """A plausible LST scene: smooth field + urban blocks + hotspots + water + gaps."""
    base = rng.uniform(-5, 45)
    amp = rng.uniform(0.5, 12)
    f = ndimage.gaussian_filter(rng.normal(size=shape), rng.uniform(2, 12))
    f = base + amp * f / (f.std() + 1e-9)
    for _ in range(rng.integers(0, 6)):                   # urban blocks
        r, c = rng.integers(0, shape[0] - 20), rng.integers(0, shape[1] - 20)
        h, w = rng.integers(5, 20, 2)
        f[r:r + h, c:c + w] += rng.uniform(1, 8)
    for _ in range(rng.integers(0, 4)):                   # point hotspots / fires
        r, c = rng.integers(0, shape[0]), rng.integers(0, shape[1])
        yy, xx = np.ogrid[:shape[0], :shape[1]]
        f += rng.uniform(3, 30) * np.exp(-((yy - r) ** 2 + (xx - c) ** 2) / (2 * rng.uniform(1, 4) ** 2))
    for _ in range(rng.integers(0, 3)):                   # cold water bodies
        r, c = rng.integers(0, shape[0] - 25), rng.integers(0, shape[1] - 25)
        f[r:r + rng.integers(5, 25), c:c + rng.integers(5, 25)] -= rng.uniform(2, 12)
    f += rng.normal(0, rng.uniform(0.05, 0.5), shape)
    if rng.random() < 0.4:                                # cloud holes / no data
        holes = ndimage.gaussian_filter(rng.normal(size=shape), 8) > rng.uniform(0.5, 1.2)
        f[holes] = np.nan
    return f


def lst_scene(seed=0, shape=(128, 128), bbox=(-119.85, 36.70, -119.75, 36.78)):
    """Synthetic LST raster (Celsius, NaN = no data) with an EPSG:4326 georef. A TEST FIXTURE."""
    lst = synthetic_lst(np.random.default_rng(seed), shape)
    h, w = shape
    dx, dy = (bbox[2] - bbox[0]) / w, (bbox[3] - bbox[1]) / h
    return lst, {"crs": "EPSG:4326", "transform": [dx, 0.0, bbox[0], 0.0, -dy, bbox[3]],
                 "width": w, "height": h}
