"""Spectral index calculations and neural-network inference."""
import numpy as np

FEATURE_NAMES = ("Blue", "Green", "Red", "NIR", "SWIR1")
INDEX_NAMES = ("NDVI", "EVI", "NDWI", "NDMI")
EPS = 1e-6


def calculate_spectral_indices(reflectance):
    """Calculate NDVI, EVI, NDWI and NDMI from (..., 5) reflectance data."""
    x = np.asarray(reflectance, dtype=np.float32)
    if x.shape[-1] != 5:
        raise ValueError("Expected Blue, Green, Red, NIR, SWIR1.")

    blue, green, red, nir, swir1 = np.moveaxis(x, -1, 0)

    with np.errstate(divide="ignore", invalid="ignore"):
        ndvi_den = nir + red
        evi_den = nir + 6 * red - 7.5 * blue + 1
        ndwi_den = green + nir
        ndmi_den = nir + swir1

        ndvi = np.where(
            np.abs(ndvi_den) > EPS, (nir - red) / ndvi_den, np.nan
        )
        evi = np.where(
            np.abs(evi_den) > EPS, 2.5 * (nir - red) / evi_den, np.nan
        )
        ndwi = np.where(
            np.abs(ndwi_den) > EPS, (green - nir) / ndwi_den, np.nan
        )
        ndmi = np.where(
            np.abs(ndmi_den) > EPS, (nir - swir1) / ndmi_den, np.nan
        )

    return np.stack([ndvi, evi, ndwi, ndmi], axis=-1).astype(np.float32)


def predict_spectral_indices(reflectance, model, scaler, batch_size=20000):
    """Predict four indices from rows of five reflectance values."""
    x = np.asarray(reflectance, dtype=np.float32)
    if x.ndim != 2 or x.shape[1] != 5:
        raise ValueError("Expected shape (n_pixels, 5).")

    result = np.full((len(x), 4), np.nan, dtype=np.float32)
    valid = np.all(np.isfinite(x), axis=1) & np.all(x >= 0, axis=1)
    indices = np.flatnonzero(valid)

    for start in range(0, len(indices), batch_size):
        idx = indices[start:start + batch_size]
        scaled = scaler.transform(x[idx]).astype(np.float32)
        result[idx] = model.predict(scaled, verbose=0)

    return result
