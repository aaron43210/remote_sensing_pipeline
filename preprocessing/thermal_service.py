"""
Preprocessing service (thermal): raw.thermal -> preprocessed.thermal.

Uses thermal/process.thermal_celsius (the scene's own MTL calibration and the
QA_PIXEL cloud mask), then reprojects the Celsius raster from the scene's UTM
zone to EPSG:4326 so every map layer of a job shares one CRS.
"""

import logging
import tempfile
from pathlib import Path

import numpy as np

from hs_common import geo, status, storage
from hs_common.kafka import Permanent, Worker, publish
from hs_common.messages import PreprocessedThermal, RawThermal
from hs_common.storage import keys
from thermal import config as tconfig
from thermal import process

log = logging.getLogger("preprocessing-thermal")


def to_wgs84(data, transform, crs, nodata):
    """Reproject (nearest, so no smearing of cloud edges) to EPSG:4326."""
    from rasterio.transform import Affine
    from rasterio.warp import Resampling, calculate_default_transform, reproject

    h, w = data.shape
    src_t = Affine(*list(transform)[:6])
    dst_t, dw, dh = calculate_default_transform(crs, "EPSG:4326", w, h,
                                                *geo.bounds_from_transform(list(src_t)[:6], w, h))
    out = np.full((dh, dw), np.nan, np.float32)
    src = np.where(data == nodata, np.nan, data).astype(np.float32)
    reproject(src, out, src_transform=src_t, src_crs=crs, dst_transform=dst_t, dst_crs="EPSG:4326",
              resampling=Resampling.nearest, src_nodata=np.nan, dst_nodata=np.nan)
    return out, list(dst_t)[:6]


def handle(topic, raw):
    msg = RawThermal(**raw)
    job = msg.job_id
    status.report(job, "preprocess", "RUNNING", "calibrating land surface temperature", task="thermal")
    with tempfile.TemporaryDirectory(prefix="landsat_") as td:
        st = Path(td) / Path(msg.st_key).name
        storage.get_file(msg.st_key, st)
        for k in (msg.qa_key, msg.mtl_key):
            if k:
                storage.get_file(k, Path(td) / Path(k).name)
        celsius, transform, crs, tags = process.thermal_celsius(str(st), bbox=None)
    lst, t4326 = to_wgs84(np.asarray(celsius), transform, crs, tconfig.FILL_VALUE)
    valid = np.isfinite(lst)
    if valid.sum() < 10:
        raise Permanent("no valid (cloud-free) thermal pixels in this area")
    key = storage.put_bytes(keys.silver(job, "thermal/lst.tif"),
                            geo.cog_bytes(lst, t4326, tags={k: v for k, v in tags.items()
                                                            if isinstance(v, (str, int, float, bool))}),
                            "image/tiff")
    h, w = lst.shape
    stats = {"valid_fraction": round(float(valid.mean()), 4),
             "lst_min_c": round(float(np.nanmin(lst)), 2), "lst_max_c": round(float(np.nanmax(lst)), 2),
             "lst_mean_c": round(float(np.nanmean(lst)), 2),
             "cloud_masked": bool(tags.get("cloud_masked")), "scaling_from_mtl": bool(tags.get("scaling_from_mtl"))}
    publish("preprocessed.thermal", PreprocessedThermal(
        job_id=job, scene_id=msg.scene_id, acquired_at=msg.acquired_at, lst_key=key,
        georef={"crs": "EPSG:4326", "transform": t4326, "width": w, "height": h},
        bbox=msg.bbox, stats=stats, source=msg.source))
    status.report(job, "inference", "RUNNING", "running HydraNet", task="thermal")


class ThermalWorker(Worker):
    def on_give_up(self, raw, err):
        status.report(raw.get("job_id", ""), "preprocess", "ERROR", f"thermal preprocessing failed: {err}",
                      task="thermal")


def _guarded(topic, raw):
    try:
        handle(topic, raw)
    except Permanent as e:
        status.report(raw.get("job_id", ""), "preprocess", "ERROR", str(e), task="thermal")
        raise


def main():
    storage.ensure_bucket()
    ThermalWorker("preprocessing-thermal", ["raw.thermal"], _guarded).run()
