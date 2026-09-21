# =============================================================
# OWNER: BAINTY KAUR
# =============================================================

"""
Thermal Processing Service — Entry Point

Consumes pre-fetched Landsat thermal LST data from Kafka topic
'thermal-raw', processes it using the thermal-processing pipeline,
stores output products in MinIO, and publishes a summary to
'thermal-processed'.

Processing performed by this service:

1. Read Landsat LST COG from MinIO
2. Validate LST in degrees Celsius
3. Calculate scene-wide thermal statistics
4. Generate 5-class thermal classification
5. Calculate global thermal Z-scores
6. Detect global hot/cold anomalies
7. Calculate local neighbourhood Z-scores
8. Detect local anomalies
9. Detect percentile-based thermal hotspots
10. Calculate Dynamic World-based SUHI when reference rasters
    are supplied in the Kafka message
11. Save GeoTIFF products to MinIO
12. Save complete summary.json
13. Publish processing summary to Kafka


Kafka input topic:
    thermal-raw


Expected input message:

{
    "scene_id": "LC09_...",
    "thermal_path": "path/to/lst.tif",
    "shape": [rows, cols],
    "bbox": [lon_min, lat_min, lon_max, lat_max],
    "sensor": "Landsat 9 TIRS",
    "metadata": {...},

    "dynamic_world": {
        "label_path": "...",
        "built_probability_path": "..."
    }
}


The Dynamic World block is optional.

IMPORTANT:
The Dynamic World rasters supplied to this service must already
be aligned to the LST raster grid:

- Same shape
- Same CRS
- Same spatial transform
- Same 30 m analysis grid


If Dynamic World rasters are not supplied, all thermal products
are still generated and SUHI is reported as unavailable.


Output bucket:
    thermal-processed


Summary bucket:
    thermal-results


Output products:

    {scene_id}/anomaly.tif
        Binary global hot-anomaly mask.
        1 = hot anomaly
        0 = background/no anomaly

    {scene_id}/classification.tif
        Five-class thermal classification.

    {scene_id}/global_z.tif
        Global thermal Z-score.

    {scene_id}/local_z.tif
        Local neighbourhood Z-score.

    {scene_id}/hotspots.tif
        Connected-component thermal hotspot map.

    {scene_id}/summary.json
        Complete processing statistics and methodology.
"""


# =============================================================
# IMPORTS
# =============================================================

import os
import json
import shutil
import tempfile
import logging
from datetime import datetime, timezone

import numpy as np
import msgpack
import rasterio

from kafka import KafkaConsumer, KafkaProducer
from prometheus_client import (
    Counter,
    Histogram,
    start_http_server,
)

from config import ThermalConfig
from lst_calculator import LSTCalculator
from anomaly_detector import ThermalAnomalyDetector
from suhi import SUHIAnalyzer


# =============================================================
# LOGGING
# =============================================================

logging.basicConfig(
    level=getattr(
        ThermalConfig,
        "LOG_LEVEL",
        logging.INFO,
    ),
    format=(
        "%(asctime)s "
        "[thermal] "
        "%(levelname)s: "
        "%(message)s"
    ),
)

logger = logging.getLogger(__name__)


# =============================================================
# PROMETHEUS METRICS
# =============================================================

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


# =============================================================
# MAIN SERVICE
# =============================================================

class ThermalProcessingService:
    """
    Main thermal processing service.

    Bainty owns the complete implementation inside
    services/thermal/.
    """

    # =========================================================
    # INITIALIZATION
    # =========================================================

    def __init__(self):

        ThermalConfig.validate()

        # -----------------------------------------------------
        # Kafka consumer
        # -----------------------------------------------------

        self.consumer = KafkaConsumer(
            ThermalConfig.KAFKA_CONSUME_TOPIC,
            bootstrap_servers=(
                ThermalConfig.KAFKA_BOOTSTRAP_SERVERS
            ),
            value_deserializer=lambda message: (
                msgpack.unpackb(
                    message,
                    raw=False,
                )
            ),
            group_id=ThermalConfig.KAFKA_GROUP_ID,
            auto_offset_reset="earliest",
        )

        # -----------------------------------------------------
        # Kafka producer
        # -----------------------------------------------------

        self.producer = KafkaProducer(
            bootstrap_servers=(
                ThermalConfig.KAFKA_BOOTSTRAP_SERVERS
            ),
            value_serializer=lambda value: (
                msgpack.packb(
                    value,
                    use_bin_type=True,
                )
            ),
        )

        # -----------------------------------------------------
        # MinIO
        # -----------------------------------------------------

        self.minio = ThermalConfig.get_minio_client()

        self._ensure_buckets()

        # -----------------------------------------------------
        # Processing modules
        # -----------------------------------------------------

        self.lst_calc = LSTCalculator()

        self.detector = ThermalAnomalyDetector()

        self.suhi_analyzer = SUHIAnalyzer()

        # -----------------------------------------------------
        # Prometheus
        # -----------------------------------------------------

        try:

            start_http_server(
                ThermalConfig.PROMETHEUS_PORT
            )

            logger.info(
                "Prometheus metrics running on port %d",
                ThermalConfig.PROMETHEUS_PORT,
            )

        except OSError:

            logger.warning(
                "Prometheus port %d already in use",
                ThermalConfig.PROMETHEUS_PORT,
            )

        logger.info(
            "Thermal processing service ready. "
            "Consuming '%s' -> producing '%s'",
            ThermalConfig.KAFKA_CONSUME_TOPIC,
            ThermalConfig.KAFKA_PRODUCE_TOPIC,
        )

    # =========================================================
    # MAIN LOOP
    # =========================================================

    def run(self):
        """
        Continuously consume thermal scenes from Kafka.
        """

        logger.info(
            "Listening on '%s'...",
            ThermalConfig.KAFKA_CONSUME_TOPIC,
        )

        for message in self.consumer:

            local_dir = tempfile.mkdtemp(
                prefix="thermal_"
            )

            try:

                data = message.value

                # -------------------------------------------------
                # Handle upstream ingestion errors
                # -------------------------------------------------

                if "error" in data:

                    logger.warning(
                        "Skipping ingestion error: %s",
                        data["error"],
                    )

                    continue

                # -------------------------------------------------
                # Required fields
                # -------------------------------------------------

                scene_id = data["scene_id"]

                thermal_path = data["thermal_path"]

                logger.info(
                    "Processing thermal scene: %s",
                    scene_id,
                )

                # -------------------------------------------------
                # Process scene
                # -------------------------------------------------

                with PROCESSING_SECONDS.time():

                    result = self._process_scene(
                        scene_id=scene_id,
                        thermal_path=thermal_path,
                        message_data=data,
                        local_dir=local_dir,
                    )

                # -------------------------------------------------
                # Kafka output
                # -------------------------------------------------

                out_message = {
                    "scene_id": scene_id,

                    "statistics": (
                        result["statistics"]
                    ),

                    "classification": (
                        result["classification"]
                    ),

                    "anomaly": (
                        result["anomaly"]
                    ),

                    "suhi": (
                        result["suhi"]
                    ),

                    "hotspots": (
                        result["hotspots"]
                    ),

                    "products_path": (
                        f"{scene_id}/"
                    ),

                    "timestamp": self._timestamp(),
                }

                self.producer.send(
                    ThermalConfig.KAFKA_PRODUCE_TOPIC,
                    value=out_message,
                )

                self.producer.flush()

                # -------------------------------------------------
                # Metrics
                # -------------------------------------------------

                SCENES_PROCESSED.inc()

                logger.info(
                    "Completed scene %s | "
                    "Mean=%.2f°C | "
                    "Std=%.2f°C | "
                    "Global hot anomalies=%d | "
                    "Local anomalies=%d | "
                    "Hotspot components=%d",
                    scene_id,
                    result["statistics"][
                        "mean_temp_c"
                    ],
                    result["statistics"][
                        "std_temp_c"
                    ],
                    result["anomaly"][
                        "hot_pixels"
                    ],
                    result["anomaly"][
                        "local_anomaly_pixels"
                    ],
                    result["hotspots"][
                        "n_hotspots"
                    ],
                )

            except Exception as exc:

                SCENES_FAILED.inc()

                logger.error(
                    "Failed to process scene: %s",
                    exc,
                    exc_info=True,
                )

            finally:

                shutil.rmtree(
                    local_dir,
                    ignore_errors=True,
                )

    # =========================================================
    # SCENE PROCESSING
    # =========================================================

    def _process_scene(
        self,
        scene_id: str,
        thermal_path: str,
        message_data: dict,
        local_dir: str,
    ) -> dict:
        """
        Execute the complete thermal processing pipeline.
        """

        # =====================================================
        # 1. DOWNLOAD LST FROM MINIO
        # =====================================================

        local_lst = os.path.join(
            local_dir,
            "lst.tif",
        )

        logger.info(
            "Downloading LST from MinIO: %s",
            thermal_path,
        )

        self.minio.fget_object(
            ThermalConfig.BUCKET_INPUT,
            thermal_path,
            local_lst,
        )

        # =====================================================
        # 2. READ LST RASTER
        # =====================================================

        with rasterio.open(local_lst) as src:

            lst_raw = src.read(1)

            transform = src.transform

            crs = src.crs

            profile = src.profile.copy()

            raster_height = src.height

            raster_width = src.width

            resolution_x = abs(
                src.transform.a
            )

            resolution_y = abs(
                src.transform.e
            )

            source_nodata = src.nodata

        logger.info(
            "Input raster: %d x %d | "
            "resolution %.2fm x %.2fm",
            raster_height,
            raster_width,
            resolution_x,
            resolution_y,
        )

        # =====================================================
        # 3. VALIDATE / CONVERT LST
        # =====================================================

        lst_celsius = (
            self.lst_calc.lst_from_celsius(
                lst_raw
            )
        )

        # -----------------------------------------------------
        # Apply source nodata
        # -----------------------------------------------------

        if source_nodata is not None:

            lst_celsius = (
                lst_celsius.astype(
                    np.float32,
                    copy=True,
                )
            )

            lst_celsius[
                lst_raw == source_nodata
            ] = np.nan

        # =====================================================
        # 4. THERMAL STATISTICS + CLASSIFICATION
        # =====================================================

        logger.info(
            "Calculating thermal statistics "
            "and classification"
        )

        indices = (
            self.lst_calc.compute_thermal_indices(
                lst_celsius
            )
        )

        # =====================================================
        # 5. GLOBAL ANOMALY DETECTION
        # =====================================================

        logger.info(
            "Calculating global thermal Z-scores"
        )

        (
            global_z,
            hot_mask,
            cold_mask,
            global_statistics,
        ) = self.detector.detect_global_anomalies(
            lst_celsius
        )

        # =====================================================
        # 6. LOCAL ANOMALY DETECTION
        # =====================================================

        logger.info(
            "Calculating local thermal anomalies"
        )

        (
            local_z,
            local_anomalies,
            local_statistics,
        ) = self.detector.detect_local_anomalies(
            lst_celsius
        )

        # =====================================================
        # 7. HOTSPOT DETECTION
        # =====================================================

        logger.info(
            "Detecting percentile-based thermal hotspots"
        )

        (
            hotspot_map,
            hotspot_info,
            hotspot_statistics,
        ) = self.detector.detect_hotspots(
            lst_celsius
        )

        # =====================================================
        # 8. SUHI ANALYSIS
        # =====================================================

        suhi_result = self._process_suhi(
            message_data=message_data,
            lst_celsius=lst_celsius,
            local_dir=local_dir,
            resolution_x=resolution_x,
        )

        # =====================================================
        # 9. SAVE OUTPUT PRODUCTS
        # =====================================================

        products = {

            # -------------------------------------------------
            # Binary global hot anomaly
            # -------------------------------------------------

            "anomaly": hot_mask.astype(
                np.uint8
            ),

            # -------------------------------------------------
            # Five-class thermal classification
            # -------------------------------------------------

            "classification": (
                indices["classification"]
                .astype(np.uint8)
            ),

            # -------------------------------------------------
            # Continuous global Z-score
            # -------------------------------------------------

            "global_z": global_z.astype(
                np.float32
            ),

            # -------------------------------------------------
            # Continuous local Z-score
            # -------------------------------------------------

            "local_z": local_z.astype(
                np.float32
            ),

            # -------------------------------------------------
            # Thermal hotspot map
            # -------------------------------------------------

            "hotspots": hotspot_map.astype(
                np.uint8
            ),
        }

        for name, array in products.items():

            output_path = os.path.join(
                local_dir,
                f"{name}.tif",
            )

            nodata = (
                self._output_nodata_for(
                    name
                )
            )

            self._save_raster(
                data=array,
                transform=transform,
                crs=crs,
                path=output_path,
                nodata=nodata,
                source_profile=profile,
            )

            self.minio.fput_object(
                ThermalConfig.BUCKET_OUTPUT,
                f"{scene_id}/{name}.tif",
                output_path,
            )

            logger.info(
                "Uploaded %s.tif",
                name,
            )

        # =====================================================
        # 10. BUILD SUMMARY
        # =====================================================

        statistics = {

            "mean_temp_c": round(
                float(
                    indices["mean"]
                ),
                4,
            ),

            "std_temp_c": round(
                float(
                    indices["std"]
                ),
                4,
            ),

            "min_temp_c": round(
                float(
                    indices["min"]
                ),
                4,
            ),

            "max_temp_c": round(
                float(
                    indices["max"]
                ),
                4,
            ),

            "valid_pixels": int(
                indices.get(
                    "n_valid_pixels",
                    np.sum(
                        np.isfinite(
                            lst_celsius
                        )
                    ),
                )
            ),
        }

        # =====================================================
        # CLASSIFICATION SUMMARY
        # =====================================================

        classification_array = (
            indices["classification"]
        )

        classification_counts = {
            str(class_id): int(
                np.sum(
                    classification_array
                    == class_id
                )
            )
            for class_id in range(1, 6)
        }

        classification_thresholds = (
            indices.get(
                "classification_thresholds",
                {},
            )
        )

        classification_summary = {

            "method": (
                "Mean ± standard deviation "
                "thermal classification"
            ),

            "classes": {
                "1": "Very Low",
                "2": "Low",
                "3": "Moderate",
                "4": "High",
                "5": "Very High",
            },

            "thresholds_c": {
                str(key): float(value)
                for key, value
                in classification_thresholds.items()
            },

            "pixel_counts": (
                classification_counts
            ),
        }

        # =====================================================
        # ANOMALY SUMMARY
        # =====================================================

        anomaly_summary = {

            "global_z_threshold": float(
                global_statistics.get(
                    "threshold",
                    ThermalConfig.GLOBAL_Z_THRESHOLD,
                )
            ),

            "hot_pixels": int(
                np.sum(hot_mask)
            ),

            "cold_pixels": int(
                np.sum(cold_mask)
            ),

            "local_anomaly_pixels": int(
                np.sum(local_anomalies)
            ),

            "global_statistics": (
                self._json_safe(
                    global_statistics
                )
            ),

            "local_statistics": (
                self._json_safe(
                    local_statistics
                )
            ),
        }

        # =====================================================
        # HOTSPOT SUMMARY
        # =====================================================

        hotspot_summary = {

            "n_hotspots": int(
                len(hotspot_info)
            ),

            "statistics": (
                self._json_safe(
                    hotspot_statistics
                )
            ),

            "components": (
                self._json_safe(
                    hotspot_info
                )
            ),
        }

        # =====================================================
        # COMPLETE SUMMARY
        # =====================================================

        metadata = (
            message_data.get(
                "metadata",
                {},
            )
        )

        summary = {

            "scene_id": scene_id,

            "timestamp": self._timestamp(),

            "sensor": message_data.get(
                "sensor",
                "Landsat 9 TIRS",
            ),

            "study_area": metadata.get(
                "study_area",
                None,
            ),

            "acquisition_date": (
                metadata.get(
                    "acquisition_date",
                    None,
                )
            ),

            "raster": {

                "rows": raster_height,

                "cols": raster_width,

                "resolution_x_m": (
                    resolution_x
                ),

                "resolution_y_m": (
                    resolution_y
                ),

                "crs": (
                    crs.to_string()
                    if crs is not None
                    else None
                ),
            },

            "statistics": statistics,

            "classification": (
                classification_summary
            ),

            "anomaly": anomaly_summary,

            "suhi": self._json_safe(
                suhi_result
            ),

            "hotspots": hotspot_summary,

            "methodology": {

                "lst_input": (
                    "Landsat Collection 2 "
                    "Level-2 surface temperature "
                    "in Celsius"
                ),

                "global_anomaly": (
                    "Global Z-score with threshold "
                    "Z >= 2 for hot anomalies and "
                    "Z <= -2 for cold anomalies"
                ),

                "local_anomaly": (
                    "Valid-pixel-aware local "
                    "Z-score using an 11x11 "
                    "neighbourhood"
                ),

                "thermal_classification": (
                    "Five classes based on scene "
                    "mean and standard deviation"
                ),

                "hotspot_detection": (
                    "95th percentile LST threshold "
                    "with 8-connected components "
                    "and minimum component size"
                ),

                "suhi": (
                    "Dynamic World land-cover based "
                    "urban-rural mean LST difference"
                ),
            },
        }

        # =====================================================
        # 11. SAVE SUMMARY JSON
        # =====================================================

        summary_path = os.path.join(
            local_dir,
            "summary.json",
        )

        with open(
            summary_path,
            "w",
            encoding="utf-8",
        ) as file:

            json.dump(
                self._json_safe(
                    summary
                ),
                file,
                indent=2,
            )

        self.minio.fput_object(
            ThermalConfig.BUCKET_RESULTS,
            f"{scene_id}/summary.json",
            summary_path,
        )

        logger.info(
            "Uploaded summary.json"
        )

        # =====================================================
        # 12. RETURN KAFKA SUMMARY
        # =====================================================

        return {

            "statistics": statistics,

            "classification": (
                classification_summary
            ),

            "anomaly": anomaly_summary,

            "suhi": self._json_safe(
                suhi_result
            ),

            "hotspots": hotspot_summary,
        }

    # =========================================================
    # SUHI PROCESSING
    # =========================================================

    def _process_suhi(
        self,
        message_data: dict,
        lst_celsius: np.ndarray,
        local_dir: str,
        resolution_x: float,
    ) -> dict:
        """
        Run Dynamic World-based SUHI analysis when the
        required reference rasters are supplied.

        Expected input:

        "dynamic_world": {
            "label_path": "...",
            "built_probability_path": "..."
        }

        The paths are MinIO object paths inside
        thermal-data.

        Dynamic World rasters must already be aligned
        to the LST raster grid.
        """

        dynamic_world = (
            message_data.get(
                "dynamic_world"
            )
        )

        # -----------------------------------------------------
        # Dynamic World data not supplied
        # -----------------------------------------------------

        if not dynamic_world:

            return {

                "status": "not_available",

                "reason": (
                    "Dynamic World label and built "
                    "probability rasters were not "
                    "provided in the Kafka message."
                ),
            }

        label_path = (
            dynamic_world.get(
                "label_path"
            )
        )

        built_probability_path = (
            dynamic_world.get(
                "built_probability_path"
            )
        )

        # -----------------------------------------------------
        # Missing Dynamic World paths
        # -----------------------------------------------------

        if (
            not label_path
            or not built_probability_path
        ):

            return {

                "status": "not_available",

                "reason": (
                    "Both label_path and "
                    "built_probability_path are "
                    "required for Dynamic World SUHI."
                ),
            }

        # -----------------------------------------------------
        # Download Dynamic World rasters
        # -----------------------------------------------------

        label_local = os.path.join(
            local_dir,
            "dynamic_world_label.tif",
        )

        probability_local = os.path.join(
            local_dir,
            "dynamic_world_built_probability.tif",
        )

        logger.info(
            "Downloading Dynamic World label: %s",
            label_path,
        )

        self.minio.fget_object(
            ThermalConfig.BUCKET_INPUT,
            label_path,
            label_local,
        )

        logger.info(
            "Downloading Dynamic World built "
            "probability: %s",
            built_probability_path,
        )

        self.minio.fget_object(
            ThermalConfig.BUCKET_INPUT,
            built_probability_path,
            probability_local,
        )

        # -----------------------------------------------------
        # Read Dynamic World arrays
        # -----------------------------------------------------

        with rasterio.open(
            label_local
        ) as src:

            dw_label = src.read(1)

            dw_label_shape = src.shape

            dw_label_crs = src.crs

            dw_label_transform = (
                src.transform
            )

        with rasterio.open(
            probability_local
        ) as src:

            dw_probability = src.read(1)

            dw_probability_shape = src.shape

            dw_probability_crs = src.crs

            dw_probability_transform = (
                src.transform
            )

        logger.info(
            "Dynamic World label: shape=%s CRS=%s",
            dw_label_shape,
            dw_label_crs,
        )

        logger.info(
            "Dynamic World probability: "
            "shape=%s CRS=%s",
            dw_probability_shape,
            dw_probability_crs,
        )

        # -----------------------------------------------------
        # Shape validation
        # -----------------------------------------------------

        if (
            dw_label.shape
            != lst_celsius.shape
        ):

            raise ValueError(
                "Dynamic World label raster shape "
                f"{dw_label.shape} does not match "
                f"LST shape {lst_celsius.shape}. "
                "Dynamic World must be aligned to "
                "the LST grid before processing."
            )

        if (
            dw_probability.shape
            != lst_celsius.shape
        ):

            raise ValueError(
                "Dynamic World built probability "
                f"shape {dw_probability.shape} "
                "does not match "
                f"LST shape {lst_celsius.shape}. "
                "Dynamic World must be aligned to "
                "the LST grid before processing."
            )

        # -----------------------------------------------------
        # Optional grid validation
        #
        # We already require matching shape. We also validate
        # CRS and transform against the LST raster whenever
        # metadata is available.
        # -----------------------------------------------------

        # Read LST spatial reference again from the
        # temporary local raster.

        local_lst = os.path.join(
            local_dir,
            "lst.tif",
        )

        with rasterio.open(
            local_lst
        ) as src:

            lst_crs = src.crs

            lst_transform = src.transform

        if (
            dw_label_crs is not None
            and lst_crs is not None
            and dw_label_crs != lst_crs
        ):

            raise ValueError(
                "Dynamic World label CRS "
                f"{dw_label_crs} does not match "
                f"LST CRS {lst_crs}."
            )

        if (
            dw_label_transform
            != lst_transform
        ):

            raise ValueError(
                "Dynamic World label transform "
                "does not match the LST spatial grid."
            )

        if (
            dw_probability_crs is not None
            and lst_crs is not None
            and dw_probability_crs != lst_crs
        ):

            raise ValueError(
                "Dynamic World built probability CRS "
                f"{dw_probability_crs} does not match "
                f"LST CRS {lst_crs}."
            )

        if (
            dw_probability_transform
            != lst_transform
        ):

            raise ValueError(
                "Dynamic World built probability "
                "transform does not match the "
                "LST spatial grid."
            )

        # -----------------------------------------------------
        # Run Dynamic World SUHI
        # -----------------------------------------------------

        suhi = (
            self.suhi_analyzer
            .analyze_dynamic_world_suhi(
                lst_celsius=lst_celsius,
                dynamic_world_label=dw_label,
                dynamic_world_built_probability=(
                    dw_probability
                ),
            )
        )

        return suhi

    # =========================================================
    # RASTER OUTPUT
    # =========================================================

    def _save_raster(
        self,
        data: np.ndarray,
        transform,
        crs,
        path: str,
        nodata,
        source_profile: dict,
    ):
        """
        Save a raster using the input spatial reference,
        tiled GeoTIFF layout and LZW compression.
        """

        data = np.asarray(data)

        # -----------------------------------------------------
        # Convert NaN to output nodata
        # -----------------------------------------------------

        if np.issubdtype(
            data.dtype,
            np.floating,
        ):

            data = data.astype(
                np.float32,
                copy=True,
            )

            data[
                ~np.isfinite(data)
            ] = nodata

        else:

            data = data.copy()

        # -----------------------------------------------------
        # Output profile
        # -----------------------------------------------------

        profile = {

            "driver": "GTiff",

            "height": data.shape[0],

            "width": data.shape[1],

            "count": 1,

            "dtype": str(
                data.dtype
            ),

            "crs": crs,

            "transform": transform,

            "nodata": nodata,

            "tiled": True,

            "blockxsize": (
                ThermalConfig.RASTER_TILE_SIZE
            ),

            "blockysize": (
                ThermalConfig.RASTER_TILE_SIZE
            ),

            "compress": (
                ThermalConfig.RASTER_COMPRESSION
            ),
        }

        with rasterio.open(
            path,
            "w",
            **profile,
        ) as dst:

            dst.write(
                data,
                1,
            )

    # =========================================================
    # OUTPUT NODATA
    # =========================================================

    @staticmethod
    def _output_nodata_for(
        product_name: str,
    ):
        """
        Define nodata according to product type.
        """

        if product_name in {
            "classification",
            "anomaly",
            "hotspots",
        }:

            return 0

        return ThermalConfig.OUTPUT_NODATA

    # =========================================================
    # MINIO BUCKETS
    # =========================================================

    def _ensure_buckets(self):
        """
        Ensure output and summary buckets exist.
        """

        for bucket in [
            ThermalConfig.BUCKET_OUTPUT,
            ThermalConfig.BUCKET_RESULTS,
        ]:

            if not self.minio.bucket_exists(
                bucket
            ):

                self.minio.make_bucket(
                    bucket
                )

                logger.info(
                    "Created MinIO bucket: %s",
                    bucket,
                )

    # =========================================================
    # TIMESTAMP
    # =========================================================

    @staticmethod
    def _timestamp():
        """
        Return timezone-aware UTC timestamp.
        """

        return datetime.now(
            timezone.utc
        ).isoformat()

    # =========================================================
    # JSON SERIALIZATION
    # =========================================================

    @staticmethod
    def _json_safe(value):
        """
        Convert NumPy values and arrays into
        JSON-safe Python objects.
        """

        if isinstance(
            value,
            dict,
        ):

            return {
                str(key): (
                    ThermalProcessingService
                    ._json_safe(item)
                )
                for key, item in value.items()
            }

        if isinstance(
            value,
            (list, tuple),
        ):

            return [
                ThermalProcessingService
                ._json_safe(item)
                for item in value
            ]

        if isinstance(
            value,
            np.ndarray,
        ):

            return value.tolist()

        if isinstance(
            value,
            np.integer,
        ):

            return int(value)

        if isinstance(
            value,
            np.floating,
        ):

            return float(value)

        if isinstance(
            value,
            np.bool_,
        ):

            return bool(value)

        if (
            isinstance(value, float)
            and not np.isfinite(value)
        ):

            return None

        return value


# =============================================================
# ENTRY POINT
# =============================================================

if __name__ == "__main__":

    service = ThermalProcessingService()

    service.run()