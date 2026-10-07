"""Thermal-only ROI validation and feature extraction.

The frontend/API can pass a selected-area manifest into this module, but this
module has no dependency on the shared hyperspectral or mineral services.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import rasterio


REQUIRED_MANIFEST_FIELDS = {
    "scene_id",
    "thermal_path",
    "sensor",
    "source_file",
    "bbox",
    "crs",
    "units",
    "shape",
}


def _validate_bbox(bbox: Any) -> tuple[float, float, float, float]:
    if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
        raise ValueError("Manifest bbox must contain [lon_min, lat_min, lon_max, lat_max].")

    values = [float(value) for value in bbox]
    lon_min, lat_min, lon_max, lat_max = values
    if not (lon_min < lon_max and lat_min < lat_max):
        raise ValueError("Manifest bbox must have lon_min < lon_max and lat_min < lat_max.")
    return lon_min, lat_min, lon_max, lat_max


def validate_manifest(
    manifest_path: str | Path,
    raster_path: str | Path,
) -> dict[str, Any]:
    """Validate the thermal manifest and its selected-area ROI.

    The raster is expected to contain the ST_B10 surface-temperature band.
    The manifest's bbox is checked against the raster's geospatial extent.
    """

    manifest_file = Path(manifest_path)
    raster_file = Path(raster_path)
    if not manifest_file.is_file():
        raise FileNotFoundError(f"Thermal manifest not found: {manifest_file}")
    if not raster_file.is_file():
        raise FileNotFoundError(f"Thermal raster not found: {raster_file}")

    with manifest_file.open(encoding="utf-8") as handle:
        manifest = json.load(handle)

    missing = REQUIRED_MANIFEST_FIELDS - manifest.keys()
    if missing:
        raise ValueError(f"Manifest is missing required fields: {sorted(missing)}")

    sensor = str(manifest["sensor"]).upper()
    if "ST_B10" not in sensor and "BAND 10" not in sensor.upper():
        raise ValueError(f"Expected an ST_B10/Band 10 thermal raster, got: {sensor}")

    bbox = _validate_bbox(manifest["bbox"])
    manifest_path_name = Path(str(manifest["thermal_path"])).name
    raster_path_name = raster_file.name
    if manifest_path_name != raster_path_name and manifest_path_name != "":
        raise ValueError(
            "Manifest thermal_path must refer to the supplied ST_B10 raster."
        )

    with rasterio.open(raster_file) as source:
        if source.count != 1:
            raise ValueError(
                f"Expected exactly one ST_B10 band, found {source.count}."
            )
        raster_bounds = tuple(source.bounds)
        raster_min_x, raster_min_y, raster_max_x, raster_max_y = raster_bounds

        if not (
            bbox[0] >= raster_min_x
            and bbox[1] >= raster_min_y
            and bbox[2] <= raster_max_x
            and bbox[3] <= raster_max_y
        ):
            raise ValueError(
                "Manifest bbox does not match raster extent: "
                f"bbox={bbox}, raster_bounds={tuple(raster_bounds)}"
            )

        expected_shape = list(manifest["shape"])
        if len(expected_shape) != 2 or tuple(expected_shape) != (source.height, source.width):
            raise ValueError(
                "Manifest shape does not match raster dimensions: "
                f"manifest={expected_shape}, raster={(source.height, source.width)}"
            )

        manifest["shape"] = [source.height, source.width]
        manifest["raster_bounds"] = list(raster_bounds)
        manifest["source_crs"] = source.crs.to_string() if source.crs else None
        manifest["source_resolution"] = [source.res[0], source.res[1]]
        manifest["source_nodata"] = source.nodata

    manifest["bbox"] = list(bbox)
    return manifest


def _thermal_classification(values: np.ndarray) -> np.ndarray:
    """Create a stable five-class label from the single-band ST_B10 values."""

    valid = values[np.isfinite(values)]
    if valid.size == 0:
        return np.zeros(values.shape, dtype=np.uint8)

    low, high = np.percentile(valid, [10, 90])
    class_labels = np.zeros(values.shape, dtype=np.uint8)
    class_labels[values < low] = 1
    class_labels[(values >= low) & (values < np.median(valid))] = 2
    class_labels[(values >= np.median(valid)) & (values < high)] = 3
    class_labels[values >= high] = 4
    return class_labels


def build_thermal_features(
    raster_path: str | Path,
    manifest_path: str | Path,
    output_csv: str | Path,
    max_samples: int | None = None,
    window_size: int = 3,
) -> pd.DataFrame:
    """Create a sanitizer-tested, row-level thermal feature table.

    The only raw band used is ST_B10. Derived local statistics are calculated
    from that same band so the CSV can be consumed by a thermal-specific model.
    """

    manifest = validate_manifest(manifest_path, raster_path)
    raster_file = Path(raster_path)
    output_csv = Path(output_csv)

    with rasterio.open(raster_file) as source:
        raw = source.read(1).astype(np.float32)
        transform = source.transform
        crs = source.crs
        nodata = source.nodata

    if raw.ndim != 2 or raw.size == 0:
        raise ValueError("ST_B10 raster must be a non-empty two-dimensional array.")

    if nodata is not None:
        raw[raw == nodata] = np.nan
    raw = np.nan_to_num(raw, nan=np.nan, posinf=np.nan, neginf=np.nan)

    if window_size < 1 or window_size % 2 == 0:
        raise ValueError("window_size must be a positive odd integer.")

    pad = window_size // 2
    local_mean = np.empty_like(raw)
    local_std = np.empty_like(raw)
    local_min = np.empty_like(raw)
    local_max = np.empty_like(raw)
    local_range = np.empty_like(raw)

    for row in range(raw.shape[0]):
        for col in range(raw.shape[1]):
            window = raw[
                max(0, row - pad) : min(raw.shape[0], row + pad + 1),
                max(0, col - pad) : min(raw.shape[1], col + pad + 1),
            ]
            valid_window = window[np.isfinite(window)]
            if valid_window.size:
                local_mean[row, col] = np.mean(valid_window)
                local_std[row, col] = np.std(valid_window)
                local_min[row, col] = np.min(valid_window)
                local_max[row, col] = np.max(valid_window)
                local_range[row, col] = np.ptp(valid_window)
            else:
                local_mean[row, col] = np.nan
                local_std[row, col] = np.nan
                local_min[row, col] = np.nan
                local_max[row, col] = np.nan
                local_range[row, col] = np.nan

    valid_mask = np.isfinite(raw) & np.isfinite(local_mean) & np.isfinite(local_std)
    rows, cols = np.nonzero(valid_mask)
    if rows.size == 0:
        raise ValueError("No valid ST_B10 pixels remain after feature extraction.")

    if max_samples is not None:
        if max_samples < 1:
            raise ValueError("max_samples must be positive.")
        if rows.size > max_samples:
            rng = np.random.default_rng(42)
            selected = rng.choice(rows.size, size=max_samples, replace=False)
            rows = rows[selected]
            cols = cols[selected]

    thermal_classification = _thermal_classification(raw)
    thermal_classification = np.where(
        thermal_classification == 4,
        3,
        thermal_classification - 1,
    )
    features = pd.DataFrame(
        {
            "sample_id": np.arange(1, len(rows) + 1),
            "scene_id": manifest["scene_id"],
            "row": rows,
            "col": cols,
            "x": rasterio.transform.xy(transform, rows, cols, offset="center")[0],
            "y": rasterio.transform.xy(transform, rows, cols, offset="center")[1],
            "lst": raw[rows, cols],
            "local_mean_3x3": local_mean[rows, cols],
            "local_std_3x3": local_std[rows, cols],
            "local_min_3x3": local_min[rows, cols],
            "local_max_3x3": local_max[rows, cols],
            "local_range_3x3": local_range[rows, cols],
            "thermal_class": thermal_classification[rows, cols],
        }
    )
    features["thermal_class"] = features["thermal_class"].to_numpy()[np.arange(len(features))]
    features["thermal_class"] = features["thermal_class"].astype(np.uint8)
    features["source_band"] = "ST_B10"
    features["crs"] = str(crs) if crs else None
    features["roi_bbox"] = json.dumps(manifest["bbox"])

    output_csv.parent.mkdir(parents=True, exist_ok=True)
    features.to_csv(output_csv, index=False)
    return features
