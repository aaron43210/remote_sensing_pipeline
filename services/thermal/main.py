# =============================================================
# OWNER: BAINTY KAUR
# =============================================================
"""
Thermal Processing Service — Entry Point

Reads pre-fetched Landsat thermal data from Kafka topic 'thermal-raw'
(published by ANANTAHANARAYANAN's ingestion service), processes it
through physics-based algorithms, and publishes results to 'thermal-processed'.

Kafka contract:
    Consumes: 'thermal-raw'
        {
            scene_id:     str
            thermal_path: str   MinIO path to LST COG (in °C)
            shape:        [rows, cols]
            bbox:         [lon_min, lat_min, lon_max, lat_max]
            sensor:       str   e.g. "Landsat 9 TIRS"
            metadata:     dict  includes temp_min_c, temp_max_c
        }

    Produces: 'thermal-processed'
        {
            scene_id:      str
            statistics:    { mean_temp_c, std_temp_c, min_temp_c, max_temp_c }
            uhi:           { uhi_intensity_c, classification, urban_mean_c, ... }
            n_hotspots:    int
            products_path: str   MinIO prefix for output COGs
            timestamp:     str
        }

Output files saved to MinIO (bucket: thermal-processed):
    {scene_id}/anomaly.tif         — deviation from scene mean (°C)
    {scene_id}/classification.tif  — 1–5 thermal class map
    {scene_id}/global_z.tif        — global z-scores
    {scene_id}/local_z.tif         — local neighbourhood z-scores
    {scene_id}/hotspots.tif        — labelled hotspot regions
    {scene_id}/summary.json        — full statistics JSON

Owned by: BAINTY KAUR
"""

import os
import json
import shutil
import tempfile
import logging
from datetime import datetime

import numpy as np
import msgpack
import rasterio
from minio import Minio
from kafka import KafkaConsumer, KafkaProducer
from prometheus_client import Counter, Histogram, start_http_server

from config import ThermalConfig
from lst_calculator import LSTCalculator
from emissivity import EmissivityCalculator
from anomaly_detector import ThermalAnomalyDetector

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=getattr(logging, ThermalConfig.LOG_LEVEL, logging.INFO),
    format="%(asctime)s [thermal] %(levelname)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ── Prometheus metrics ───────────────────────────────────────────────────────
SCENES_PROCESSED = Counter(
    "thermal_scenes_processed_total",
    "Total thermal scenes processed successfully",
)
SCENES_FAILED = Counter(
    "thermal_scenes_failed_total",
    "Total thermal scenes that failed processing",
)
PROCESSING_SECONDS = Histogram(
    "thermal_processing_seconds",
    "Time to process one thermal scene",
)


class ThermalProcessingService:
    """
    Core thermal processing service.

    Bainty owns everything in this class and the modules it imports.
    Nothing outside services/thermal/ needs to be modified.
    """

    def __init__(self):
        ThermalConfig.validate()

        # ── Kafka ────────────────────────────────────────────────────────
        self.consumer = KafkaConsumer(
            ThermalConfig.KAFKA_CONSUME_TOPIC,
            bootstrap_servers=ThermalConfig.KAFKA_BOOTSTRAP_SERVERS,
            value_deserializer=lambda m: msgpack.unpackb(m, raw=False),
            group_id=ThermalConfig.KAFKA_GROUP_ID,
            auto_offset_reset="earliest",
        )

        self.producer = KafkaProducer(
            bootstrap_servers=ThermalConfig.KAFKA_BOOTSTRAP_SERVERS,
            value_serializer=lambda v: msgpack.packb(v, use_bin_type=True),
        )

        # ── MinIO ────────────────────────────────────────────────────────
        self.minio = ThermalConfig.get_minio_client()
        self._ensure_buckets()

        # ── Physics processors ───────────────────────────────────────────
        self.lst_calc  = LSTCalculator()
        self.emissivity = EmissivityCalculator()
        self.detector  = ThermalAnomalyDetector()

        # ── Prometheus ───────────────────────────────────────────────────
        try:
            start_http_server(ThermalConfig.PROMETHEUS_PORT)
            logger.info("Prometheus metrics on port %d", ThermalConfig.PROMETHEUS_PORT)
        except OSError:
            logger.warning("Prometheus port %d already in use", ThermalConfig.PROMETHEUS_PORT)

        logger.info(
            "Thermal processing service ready. "
            "Consuming '%s' → producing '%s'",
            ThermalConfig.KAFKA_CONSUME_TOPIC,
            ThermalConfig.KAFKA_PRODUCE_TOPIC,
        )

    def run(self):
        """Main loop — process thermal scenes as they arrive from Kafka."""
        logger.info("Listening on '%s'...", ThermalConfig.KAFKA_CONSUME_TOPIC)

        for message in self.consumer:
            local_dir = tempfile.mkdtemp(prefix="thermal_")
            try:
                data = message.value

                if "error" in data:
                    logger.warning("Skipping ingestion error: %s", data["error"])
                    continue

                scene_id     = data["scene_id"]
                thermal_path = data["thermal_path"]

                logger.info("Processing scene: %s", scene_id)

                with PROCESSING_SECONDS.time():
                    result = self._process_scene(
                        scene_id, thermal_path, local_dir
                    )

                # Publish result summary to Kafka
                out_msg = {
                    "scene_id":      scene_id,
                    "statistics":    result["statistics"],
                    "uhi":           result["uhi_details"],
                    "n_hotspots":    result["n_hotspots"],
                    "products_path": f"{scene_id}/",
                    "timestamp":     datetime.now().isoformat(),
                }
                self.producer.send(ThermalConfig.KAFKA_PRODUCE_TOPIC, value=out_msg)
                self.producer.flush()

                SCENES_PROCESSED.inc()
                logger.info(
                    "Done: %s  T=%.1f±%.1f°C  UHI=%.1f°C (%s)  hotspots=%d",
                    scene_id,
                    result["statistics"]["mean_temp_c"],
                    result["statistics"]["std_temp_c"],
                    result["uhi_details"].get("uhi_intensity_c", 0),
                    result["uhi_details"].get("classification", "N/A"),
                    result["n_hotspots"],
                )

            except Exception as exc:
                SCENES_FAILED.inc()
                logger.error("Failed to process scene: %s", exc, exc_info=True)

            finally:
                shutil.rmtree(local_dir, ignore_errors=True)

    # ── Private methods ──────────────────────────────────────────────────────

    def _process_scene(self, scene_id: str, thermal_path: str,
                       local_dir: str) -> dict:
        """Full processing pipeline for one scene."""

        # 1. Download COG from MinIO (thermal-data bucket, written by ingestion)
        local_lst = os.path.join(local_dir, "lst.tif")
        self.minio.fget_object(
            ThermalConfig.BUCKET_INPUT, thermal_path, local_lst
        )

        # 2. Read LST data
        with rasterio.open(local_lst) as src:
            lst_celsius = src.read(1).astype(np.float32)
            transform   = src.transform
            crs         = src.crs

        # 3. Compute thermal statistics + classification
        indices = self.lst_calc.compute_thermal_indices(lst_celsius)

        # 4. Anomaly detection
        global_z, hot_mask, cold_mask = self.detector.detect_global_anomalies(
            lst_celsius
        )
        local_z, local_anomalies = self.detector.detect_local_anomalies(
            lst_celsius
        )
        uhi_intensity, uhi_details = self.detector.compute_uhi_intensity(
            lst_celsius
        )
        hotspot_map, hotspot_info = self.detector.detect_hotspots(lst_celsius)

        # 5. Save product rasters to MinIO
        products = {
            "anomaly":        indices["thermal_anomaly"].astype(np.float32),
            "classification": indices["classification"].astype(np.float32),
            "global_z":       global_z,
            "local_z":        local_z,
            "hotspots":       hotspot_map.astype(np.float32),
        }

        for name, arr in products.items():
            out_tif = os.path.join(local_dir, f"{name}.tif")
            self._save_cog(arr, transform, crs, out_tif)
            self.minio.fput_object(
                ThermalConfig.BUCKET_OUTPUT,
                f"{scene_id}/{name}.tif",
                out_tif,
            )

        # 6. Build and save summary JSON
        statistics = {
            "mean_temp_c": round(indices["mean"], 2),
            "std_temp_c":  round(indices["std"],  2),
            "min_temp_c":  round(indices["min"],  2),
            "max_temp_c":  round(indices["max"],  2),
        }
        summary = {
            "scene_id":    scene_id,
            "timestamp":   datetime.now().isoformat(),
            "sensor":      "Landsat 9 TIRS",
            "statistics":  statistics,
            "anomaly": {
                "hot_pixels":           int(np.sum(hot_mask)),
                "cold_pixels":          int(np.sum(cold_mask)),
                "local_anomaly_pixels": int(np.sum(local_anomalies)),
            },
            "uhi":      uhi_details,
            "hotspots": hotspot_info,
        }

        summary_path = os.path.join(local_dir, "summary.json")
        with open(summary_path, "w") as f:
            json.dump(summary, f, indent=2)

        self.minio.fput_object(
            ThermalConfig.BUCKET_RESULTS,
            f"{scene_id}/summary.json",
            summary_path,
        )

        return {
            "statistics":   statistics,
            "uhi_details":  uhi_details,
            "n_hotspots":   len(hotspot_info),
        }

    def _save_cog(self, data: np.ndarray, transform, crs, path: str):
        """Write a 2-D array as a tiled, LZW-compressed GeoTIFF (COG-style)."""
        with rasterio.open(
            path, "w",
            driver="GTiff",
            height=data.shape[0],
            width=data.shape[1],
            count=1,
            dtype="float32",
            crs=crs,
            transform=transform,
            tiled=True,
            blockxsize=256,
            blockysize=256,
            compress="lzw",
        ) as dst:
            dst.write(data, 1)

    def _ensure_buckets(self):
        """Create MinIO output buckets if they do not exist."""
        for bucket in [ThermalConfig.BUCKET_OUTPUT, ThermalConfig.BUCKET_RESULTS]:
            if not self.minio.bucket_exists(bucket):
                self.minio.make_bucket(bucket)
                logger.info("Created MinIO bucket: %s", bucket)


if __name__ == "__main__":
    service = ThermalProcessingService()
    service.run()
