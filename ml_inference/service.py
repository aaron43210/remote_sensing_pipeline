"""
ML inference service: preprocessed.{hyperspectral,thermal} -> ml.results.

Every map is produced by HydraNet (ONNX Runtime) through hydra.maps, the same
code path that verify_maps.py proves equal to the classical methods. On each
job a pixel sample is ALSO run through the classical method (shadow
verification); the agreement is attached to the result and exported to
Prometheus, so the claim "the network produces the normal method's map" is
checked continuously in production, not only at training time.
"""

import logging
import os
import time

import numpy as np
import redis
import zarr

from hs_common import logs, metrics, status, storage
from hs_common.config import settings
from hs_common.geo import read_cog
from hs_common.kafka import Permanent, Worker, publish
from hs_common.messages import Layer, MLResult, PreprocessedHyperspectral, PreprocessedThermal
from hs_common.storage import keys
from hydra import maps, spec
from hydra.features.masks import indices

import layers
from model_store import ModelStore

log = logging.getLogger("ml_inference")
SHADOW_PIXELS = int(os.getenv("SHADOW_SAMPLE_PIXELS", "1500"))
MODELS = ModelStore()
_redis = redis.Redis.from_url(settings.redis_url, socket_timeout=5)


def _sample_mask(mask, n, seed=0):
    flat = np.flatnonzero(mask.ravel())
    if flat.size > n:
        flat = np.random.default_rng(seed).choice(flat, n, replace=False)
    out = np.zeros(mask.size, bool)
    out[flat] = True
    return out.reshape(mask.shape)


def _restrict(map_dict, sample):
    """Keep only the sampled pixels of every map (others become 'not mapped')."""
    out = {}
    for k, v in map_dict.items():
        if v.dtype == np.uint8:
            out[k] = np.where(sample, v, maps.NOT_MAPPED).astype(np.uint8)
        elif v.ndim == 3:
            out[k] = np.where(sample[..., None], v, np.nan)
        else:
            out[k] = np.where(sample, v, np.nan)
    return out


ABUNDANCE_MIN_MEAN = 0.02     # an abundance layer is written only for minerals present in the area
CLASS_MIN_FRACTION = 0.01


def _present(nn, bare, i):
    """Mineral i covers >= 2 % on average, or is the dominant class of >= 1 % of bare pixels."""
    if not bare.any():
        return False
    return bool(np.nanmean(nn["mineral_abundance"][..., i][bare]) >= ABUNDANCE_MIN_MEAN
                or (nn["mineral_class"][bare] == i).mean() >= CLASS_MIN_FRACTION)


def _shadow(nn_maps, ref_maps, task):
    agr = maps.agreement(nn_maps, ref_maps)
    out = {}
    for layer, m in agr.items():
        if layer.endswith("_std") or layer == "mineral_confidence":
            continue
        # one "agreement" scale for the metric and its alert: share of pixels for classes,
        # 1 - nMAE for continuous layers (R² is unstable on low-variance areas)
        value = m.get("pixel_agreement", 1 - m["nmae"] if "nmae" in m else m.get("r2"))
        out[layer] = {k: (round(v, 5) if isinstance(v, float) else v) for k, v in m.items()}
        if value is not None:
            metrics.AGREEMENT.labels(task, layer).set(value)
    return out


def _stac_item(job, task, georef, bounds, lyr, acquired_at, model_version, source):
    return {"type": "Feature", "stac_version": "1.0.0", "id": f"{job}-{task}",
            "bbox": bounds, "geometry": {"type": "Polygon", "coordinates": [[
                [bounds[0], bounds[1]], [bounds[2], bounds[1]], [bounds[2], bounds[3]],
                [bounds[0], bounds[3]], [bounds[0], bounds[1]]]]},
            "properties": {"datetime": acquired_at, "hydranet:model_version": model_version,
                           "hydranet:task": task, "hydranet:source": source, "proj:epsg": 4326},
            "assets": {l["name"]: {"href": f"s3://{storage.bucket()}/{l['tif_key']}",
                                   "type": "image/tiff; application=geotiff; profile=cloud-optimized",
                                   "roles": ["data"]} for l in lyr},
            "links": []}


def _finish(job, task, version, lyr, summary, verification, timings, acquired_at, georef, source):
    bounds = lyr[0]["bounds"] if lyr else []
    storage.put_json(keys.result(job, task, "stac_item.json"),
                     _stac_item(job, task, georef, bounds, lyr, acquired_at, version, source))
    storage.put_json(keys.result(job, task, "manifest.json"),
                     {"job_id": job, "task": task, "model_version": version, "layers": lyr,
                      "summary": summary, "verification": verification, "timings": timings,
                      "feature_spec_hash": spec.feature_spec_hash()})
    publish("ml.results", MLResult(job_id=job, task=task, model_version=version,
                                   layers=[Layer(**l) for l in lyr], summary=summary,
                                   verification=verification, timings=timings, acquired_at=acquired_at))


def hyperspectral(raw):
    msg = PreprocessedHyperspectral(**raw)
    job = msg.job_id
    version, rt, lut = MODELS.current()
    t0 = time.time()
    z = zarr.open_array(storage.zarr_store(msg.reflectance_key), mode="r")
    cube = np.asarray(z[:], dtype=np.float32)
    wl = np.asarray(msg.wavelengths)
    cube[(cube <= 0).all(axis=-1)] = np.nan                     # preprocessing's nodata = all bands 0
    cloud = storage.get_numpy(msg.cloud_key) if msg.cloud_key else None
    masks = maps.scene_masks(cube, wl, cloud)
    georef = msg.georef.model_dump()
    t_load = time.time() - t0
    common = {"granule_id": msg.granule_id, "satellite": msg.satellite, "source": msg.source,
              "pixels": int(masks.size), "cloud_fraction": round(float((masks == 0).mean()), 4)}
    ndvi, _, _ = indices(cube, wl)
    land = layers.write(job, "agriculture" if "agriculture" in msg.tasks else "mineral", "landcover", masks, georef)

    if "mineral" in msg.tasks:
        t1 = time.time()
        bare = masks == 3
        nn = maps.hydra_mineral(rt, cube, wl, bare)
        t_nn = time.time() - t1
        sample = _sample_mask(bare, SHADOW_PIXELS)
        verification = _shadow(_restrict(nn, sample), maps.classical_mineral(cube, wl, sample), "mineral")
        metrics.PIXELS.labels("mineral").inc(int(bare.sum()))
        lyr = [layers.write(job, "mineral", "mineral_class", nn["mineral_class"], georef),
               layers.write(job, "mineral", "mineral_confidence", nn["mineral_confidence"], georef),
               layers.write(job, "mineral", "mineral_quality", nn["mineral_quality"], georef)]
        for i, m in enumerate(spec.MINERALS):
            if _present(nn, bare, i):                             # no layer for minerals absent from the area
                lyr.append(layers.write(job, "mineral", f"mineral_abundance_{m}", nn["mineral_abundance"][..., i],
                                        georef, units="fraction", colormap="blue", vmin=0.0, vmax=1.0))
        if "agriculture" not in msg.tasks:
            lyr.append(land)
        summary = {**common, "mapped_fraction": round(float(bare.mean()), 4),
                   "class_fraction": lyr[0]["stats"]["class_fraction"],
                   "mean_abundance": {m: float(np.nanmean(nn["mineral_abundance"][..., i])) if bare.any() else None
                                      for i, m in enumerate(spec.MINERALS)}}
        _finish(job, "mineral", version, lyr, summary, verification,
                {"load_s": round(t_load, 3), "nn_s": round(t_nn, 3), "total_s": round(time.time() - t0, 3)},
                msg.acquired_at, georef, msg.source)

    if "agriculture" in msg.tasks:
        t1 = time.time()
        veg = masks == 2
        nn = maps.hydra_agri(rt, cube, wl, veg)
        t_nn = time.time() - t1
        sample = _sample_mask(veg, SHADOW_PIXELS)
        verification = _shadow(_restrict(nn, sample), maps.classical_agri(lut, cube, wl, sample), "agriculture")
        metrics.PIXELS.labels("agriculture").inc(int(veg.sum()))
        lyr = [land, layers.write(job, "agriculture", "ndvi", np.where(masks > 0, ndvi, np.nan), georef)]
        for p, unit in zip(spec.AGRI_PARAMS, spec.AGRI_UNITS):
            lyr.append(layers.write(job, "agriculture", f"agri_{p}", nn[f"agri_{p}"], georef, units=unit))
            lyr.append(layers.write(job, "agriculture", f"agri_{p}_std", nn[f"agri_{p}_std"], georef,
                                    units=unit, colormap="blue"))
        summary = {**common, "mapped_fraction": round(float(veg.mean()), 4),
                   "params": {p: layers._stats(nn[f"agri_{p}"]) for p in spec.AGRI_PARAMS}}
        _finish(job, "agriculture", version, lyr, summary, verification,
                {"load_s": round(t_load, 3), "nn_s": round(t_nn, 3), "total_s": round(time.time() - t0, 3)},
                msg.acquired_at, georef, msg.source)


def thermal(raw):
    msg = PreprocessedThermal(**raw)
    job = msg.job_id
    version, rt, _ = MODELS.current()
    t0 = time.time()
    lst, transform, crs, _ = read_cog(storage.get_bytes(msg.lst_key))
    lst = np.asarray(lst, dtype=np.float64)
    georef = {"crs": "EPSG:4326", "transform": transform, "width": lst.shape[1], "height": lst.shape[0]}
    try:
        nn = maps.hydra_thermal(rt, lst)
    except ValueError as e:                                      # < 10 valid pixels
        raise Permanent(str(e))
    t_nn = time.time() - t0
    verification = _shadow(nn, maps.classical_thermal(lst), "thermal")   # whole scene: cheap
    metrics.PIXELS.labels("thermal").inc(int(np.isfinite(lst).sum()))
    hot = nn["thermal_hotspots"]
    lyr = [layers.write(job, "thermal", "thermal_lst", nn["thermal_lst"], georef),
           layers.write(job, "thermal", "thermal_global_class", nn["thermal_global_class"], georef),
           layers.write(job, "thermal", "thermal_uhi_class", nn["thermal_uhi_class"], georef),
           layers.write(job, "thermal", "thermal_hotspots", np.where(np.isfinite(lst), (hot > 0), 255), georef),
           layers.write(job, "thermal", "thermal_local_anomaly",
                        np.where(np.isfinite(lst), nn["thermal_local_anomaly"], 255), georef)]
    regions = [int(r) for r in np.unique(hot) if r > 0]
    summary = {"scene_id": msg.scene_id, "granule_id": msg.scene_id, "source": msg.source,
               "uhi_intensity_c": round(nn["uhi_intensity_c"], 2), "hotspot_count": len(regions),
               "hotspot_pixels": int((hot > 0).sum()), "scene_stats": {k: (round(v, 3) if isinstance(v, float) else v)
                                                                      for k, v in nn["stats"].items()},
               **msg.stats}
    _finish(job, "thermal", version, lyr, summary, verification,
            {"nn_s": round(t_nn, 3), "total_s": round(time.time() - t0, 3)}, msg.acquired_at, georef, msg.source)


def handle(topic, raw):
    job = raw.get("job_id", "")
    if _redis.exists(f"cancelled:{job}"):
        return
    task = "thermal" if topic == "preprocessed.thermal" else None
    try:
        (thermal if task else hyperspectral)(raw)
    except Permanent as e:
        status.report(job, "inference", "ERROR", str(e), task=task)
        raise


class InferenceWorker(Worker):
    def on_give_up(self, raw, err):
        status.report(raw.get("job_id", ""), "inference", "ERROR", f"inference failed: {err}",
                      task="thermal" if "lst_key" in raw else None)


def main():
    logs.setup()
    storage.ensure_bucket()
    MODELS.wait_until_ready()
    InferenceWorker("ml_inference", ["preprocessed.hyperspectral", "preprocessed.thermal"], handle).run()


if __name__ == "__main__":
    main()
