# =============================================================
# OWNER: BAINTY KAUR
# =============================================================
"""
Thermal Processing Service — Configuration

This module contains:
1. Kafka configuration
2. MinIO configuration
3. Redis configuration
4. Monitoring configuration
5. Thermal-processing parameters
6. Output-product configuration

The thermal service consumes pre-fetched thermal/LST data from
the Kafka topic defined below and publishes processed results
back to Kafka.

No Earthdata credentials are required here because data
ingestion is handled by the upstream ingestion service.
"""

import os
import logging


logger = logging.getLogger(__name__)


# =============================================================
# ENVIRONMENT HELPERS
# =============================================================

def _get_float(name: str, default: float) -> float:
    """Read a float environment variable."""
    value = os.getenv(name)

    if value is None:
        return default

    try:
        return float(value)
    except ValueError as exc:
        raise ValueError(
            f"Environment variable {name} must be a number. "
            f"Received: {value}"
        ) from exc


def _get_int(name: str, default: int) -> int:
    """Read an integer environment variable."""
    value = os.getenv(name)

    if value is None:
        return default

    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(
            f"Environment variable {name} must be an integer. "
            f"Received: {value}"
        ) from exc


def _get_bool(name: str, default: bool) -> bool:
    """Read a boolean environment variable."""
    value = os.getenv(name)

    if value is None:
        return default

    return value.strip().lower() in {
        "1",
        "true",
        "yes",
        "y",
        "on",
    }


# =============================================================
# THERMAL CONFIGURATION
# =============================================================

class ThermalConfig:
    """
    Central configuration for the Thermal Processing Service.

    All processing thresholds are kept here so that the thermal
    algorithms remain configurable and reproducible.
    """

    # =========================================================
    # KAFKA
    # =========================================================

    KAFKA_BOOTSTRAP_SERVERS = os.getenv(
        "KAFKA_BOOTSTRAP_SERVERS",
        "kafka:29092",
    )

    # Input topic
    KAFKA_CONSUME_TOPIC = "thermal-raw"

    # Output topic
    KAFKA_PRODUCE_TOPIC = "thermal-processed"

    # Consumer group
    KAFKA_GROUP_ID = "thermal-processing-group"


    # =========================================================
    # MINIO
    # =========================================================

    MINIO_ENDPOINT = os.getenv(
        "MINIO_ENDPOINT",
        "minio:9000",
    )

    MINIO_ACCESS_KEY = os.getenv(
        "MINIO_ACCESS_KEY"
    )

    MINIO_SECRET_KEY = os.getenv(
        "MINIO_SECRET_KEY"
    )

    # Input data bucket
    BUCKET_INPUT = "thermal-data"

    # Generated raster products
    BUCKET_OUTPUT = "thermal-processed"

    # JSON summaries
    BUCKET_RESULTS = "thermal-results"


    # =========================================================
    # REDIS
    # =========================================================

    REDIS_HOST = os.getenv(
        "REDIS_HOST",
        "redis",
    )

    REDIS_PORT = _get_int(
        "REDIS_PORT",
        6379,
    )


    # =========================================================
    # MONITORING
    # =========================================================

    PROMETHEUS_PORT = _get_int(
        "PROMETHEUS_PORT",
        8092,
    )

    LOG_LEVEL = os.getenv(
        "LOG_LEVEL",
        "INFO",
    )


    # =========================================================
    # LST / THERMAL PROCESSING
    # =========================================================

    # NoData value used internally for generated raster products
    OUTPUT_NODATA = _get_float(
        "THERMAL_OUTPUT_NODATA",
        -9999.0,
    )

    # Input LST NoData value.
    # Landsat-derived LST files may use 0 as NoData in our
    # current processing workflow.
    INPUT_NODATA = _get_float(
        "THERMAL_INPUT_NODATA",
        0.0,
    )


    # =========================================================
    # THERMAL CLASSIFICATION
    # =========================================================

    # Five thermal classes based on mean ± sigma and
    # mean ± 2 sigma.

    THERMAL_CLASS_COUNT = 5

    CLASS_VERY_LOW = 1
    CLASS_LOW = 2
    CLASS_MODERATE = 3
    CLASS_HIGH = 4
    CLASS_VERY_HIGH = 5


    # =========================================================
    # GLOBAL ANOMALY
    # =========================================================

    # Global Z-score threshold:
    #
    # Z = (LST - mean) / standard deviation
    #
    # Pixels with Z >= 2 are considered positive thermal
    # anomalies.

    GLOBAL_Z_THRESHOLD = _get_float(
        "GLOBAL_Z_THRESHOLD",
        2.0,
    )


    # =========================================================
    # LOCAL ANOMALY
    # =========================================================

    # 11 × 11 pixel neighbourhood.
    #
    # At 30 m resolution:
    # 11 × 30 = 330 m neighbourhood width.

    LOCAL_WINDOW_SIZE = _get_int(
        "LOCAL_WINDOW_SIZE",
        11,
    )

    LOCAL_Z_THRESHOLD = _get_float(
        "LOCAL_Z_THRESHOLD",
        2.0,
    )


    # =========================================================
    # HOTSPOT DETECTION
    # =========================================================

    # Candidate hotspots are pixels at or above the 95th
    # percentile of valid LST values.

    HOTSPOT_PERCENTILE = _get_float(
        "HOTSPOT_PERCENTILE",
        95.0,
    )

    # Connected-component filtering.
    #
    # 9 pixels at 30 m resolution:
    # 9 × 30 × 30 = 8,100 m² = 0.81 ha

    HOTSPOT_MIN_PIXELS = _get_int(
        "HOTSPOT_MIN_PIXELS",
        9,
    )

    # Use 8-connected neighbourhood for hotspot components.
    HOTSPOT_CONNECTIVITY = 8


    # =========================================================
    # SUHI / URBAN HEAT ISLAND
    # =========================================================

    # Dynamic World built probability threshold used by the
    # validated Bathinda workflow.

    DW_BUILT_PROBABILITY_THRESHOLD = _get_float(
        "DW_BUILT_PROBABILITY_THRESHOLD",
        0.50,
    )

    # Dynamic World classes used as the rural reference:
    #
    # 1 = Trees
    # 2 = Grass
    # 4 = Crops
    # 5 = Shrub and scrub

    DW_RURAL_CLASSES = (
        1,
        2,
        4,
        5,
    )

    # Dynamic World built class
    DW_BUILT_CLASS = 6

    # Sensitivity thresholds used in the Bathinda analysis.

    SUHI_SENSITIVITY_THRESHOLDS = (
        0.40,
        0.50,
        0.60,
    )


    # =========================================================
    # OUTPUT PRODUCTS
    # =========================================================

    OUTPUT_PRODUCTS = {
        "anomaly": "anomaly.tif",
        "classification": "classification.tif",
        "global_z": "global_z.tif",
        "local_z": "local_z.tif",
        "hotspots": "hotspots.tif",
        "summary": "summary.json",
    }


    # =========================================================
    # RASTER OUTPUT
    # =========================================================

    # GeoTIFF tiling parameters.

    RASTER_TILE_SIZE = 256

    RASTER_COMPRESSION = "lzw"

    RASTER_TILED = True


    # =========================================================
    # MINIO CLIENT
    # =========================================================

    @classmethod
    def get_minio_client(cls):
        """
        Return a configured MinIO client.
        """

        from minio import Minio

        if not cls.MINIO_ACCESS_KEY:
            raise EnvironmentError(
                "MINIO_ACCESS_KEY must be set."
            )

        if not cls.MINIO_SECRET_KEY:
            raise EnvironmentError(
                "MINIO_SECRET_KEY must be set."
            )

        return Minio(
            cls.MINIO_ENDPOINT,
            access_key=cls.MINIO_ACCESS_KEY,
            secret_key=cls.MINIO_SECRET_KEY,
            secure=False,
        )


    # =========================================================
    # CONFIGURATION VALIDATION
    # =========================================================

    @classmethod
    def validate(cls):
        """
        Validate required runtime configuration.
        """

        missing = []

        if not cls.MINIO_ACCESS_KEY:
            missing.append("MINIO_ACCESS_KEY")

        if not cls.MINIO_SECRET_KEY:
            missing.append("MINIO_SECRET_KEY")

        if not cls.KAFKA_BOOTSTRAP_SERVERS:
            missing.append("KAFKA_BOOTSTRAP_SERVERS")

        if missing:
            raise EnvironmentError(
                "[thermal] Missing required environment variables: "
                f"{missing}"
            )

        # Validate thermal parameters
        if cls.LOCAL_WINDOW_SIZE < 3:
            raise ValueError(
                "LOCAL_WINDOW_SIZE must be at least 3."
            )

        if cls.LOCAL_WINDOW_SIZE % 2 == 0:
            raise ValueError(
                "LOCAL_WINDOW_SIZE must be odd."
            )

        if not 0 < cls.HOTSPOT_PERCENTILE < 100:
            raise ValueError(
                "HOTSPOT_PERCENTILE must be between 0 and 100."
            )

        if cls.HOTSPOT_MIN_PIXELS < 1:
            raise ValueError(
                "HOTSPOT_MIN_PIXELS must be >= 1."
            )

        if cls.GLOBAL_Z_THRESHOLD <= 0:
            raise ValueError(
                "GLOBAL_Z_THRESHOLD must be > 0."
            )

        if cls.LOCAL_Z_THRESHOLD <= 0:
            raise ValueError(
                "LOCAL_Z_THRESHOLD must be > 0."
            )

        if not 0 < cls.DW_BUILT_PROBABILITY_THRESHOLD <= 1:
            raise ValueError(
                "DW_BUILT_PROBABILITY_THRESHOLD must be "
                "between 0 and 1."
            )

        logger.info(
            "[thermal] Configuration validated successfully."
        )


# =============================================================
# DEFAULT CONFIGURATION SUMMARY
# =============================================================

if __name__ == "__main__":

    print("========== THERMAL CONFIGURATION ==========")

    print(
        "Kafka input :",
        ThermalConfig.KAFKA_CONSUME_TOPIC
    )

    print(
        "Kafka output:",
        ThermalConfig.KAFKA_PRODUCE_TOPIC
    )

    print(
        "Input bucket:",
        ThermalConfig.BUCKET_INPUT
    )

    print(
        "Output bucket:",
        ThermalConfig.BUCKET_OUTPUT
    )

    print(
        "Global Z threshold:",
        ThermalConfig.GLOBAL_Z_THRESHOLD
    )

    print(
        "Local window:",
        ThermalConfig.LOCAL_WINDOW_SIZE,
        "x",
        ThermalConfig.LOCAL_WINDOW_SIZE
    )

    print(
        "Local Z threshold:",
        ThermalConfig.LOCAL_Z_THRESHOLD
    )

    print(
        "Hotspot percentile:",
        ThermalConfig.HOTSPOT_PERCENTILE
    )

    print(
        "Minimum hotspot pixels:",
        ThermalConfig.HOTSPOT_MIN_PIXELS
    )

    print(
        "Dynamic World built threshold:",
        ThermalConfig.DW_BUILT_PROBABILITY_THRESHOLD
    )