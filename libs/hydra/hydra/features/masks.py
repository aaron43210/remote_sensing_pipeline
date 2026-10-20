"""Pixel masks that decide which head is shown where (same for NN and classical maps)."""

import numpy as np

from .. import spec


def _band(cube, wavelengths, nm):
    return cube[..., int(np.abs(np.asarray(wavelengths) - nm).argmin())].astype(np.float32)


def indices(cube, wavelengths):
    """NDVI (800/670), NDWI (560/860) and a nodata mask for a (..., bands) cube."""
    red, nir = _band(cube, wavelengths, 670), _band(cube, wavelengths, 800)
    green, nir2 = _band(cube, wavelengths, 560), _band(cube, wavelengths, 860)
    with np.errstate(divide="ignore", invalid="ignore"):
        ndvi = np.where(red + nir > 0, (nir - red) / (red + nir), 0.0)
        ndwi = np.where(green + nir2 > 0, (green - nir2) / (green + nir2), 0.0)
    nodata = ~np.isfinite(cube).all(axis=-1) | (np.nan_to_num(cube).max(axis=-1) <= 0)
    return ndvi.astype(np.float32), ndwi.astype(np.float32), nodata


def pixel_classes(cube, wavelengths, cloud=None):
    """
    0 nodata/cloud, 1 water, 2 vegetation (agriculture head), 3 bare (mineral head).
    """
    ndvi, ndwi, nodata = indices(cube, wavelengths)
    out = np.full(ndvi.shape, 3, dtype=np.uint8)
    out[ndvi >= spec.VEG_NDVI_MIN] = 2
    out[(ndwi > 0.0) & (ndvi < spec.VEG_NDVI_MIN)] = 1
    out[nodata] = 0
    if cloud is not None:
        out[np.asarray(cloud, dtype=bool)] = 0
    return out
