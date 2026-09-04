# =============================================================
# OWNER: BAINTY KAUR
# =============================================================
"""
Thermal Service — Local Configuration

Reads all required settings from environment variables.
This file is self-contained: it does NOT import from shared/config.py.
Bainty Kaur owns and maintains this file.

Required env vars:
    KAFKA_BOOTSTRAP_SERVERS  e.g. kafka:29092
    MINIO_ENDPOINT           e.g. minio:9000
    MINIO_ACCESS_KEY         MinIO access key
    MINIO_SECRET_KEY         MinIO secret key
    REDIS_HOST               e.g. redis
    REDIS_PORT               e.g. 6379
    PROMETHEUS_PORT          e.g. 8092

NOTE: EARTHDATA_USERNAME / EARTHDATA_PASSWORD are NOT needed here.
      Data fetching is handled by the ingestion service (ANANTAHANARAYANAN).
      This service only processes data that arrives via Kafka.
"""

import os
import logging

logger = logging.getLogger(__name__)


class ThermalConfig:
    """Read thermal service configuration from environment variables."""

    # ── Kafka ────────────────────────────────────────────
    KAFKA_BOOTSTRAP_SERVERS = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "kafka:29092")

    # Topics (read-only — owned by pipeline contract)
    KAFKA_CONSUME_TOPIC = "thermal-raw"       # Published by ingestion service
    KAFKA_PRODUCE_TOPIC = "thermal-processed" # Consumed by API gateway / fusion
    KAFKA_GROUP_ID = "thermal-processing-group"

    # ── MinIO ────────────────────────────────────────────
    MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "minio:9000")
    MINIO_ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY")
    MINIO_SECRET_KEY = os.getenv("MINIO_SECRET_KEY")

    # Buckets (read-only from ingestion, read-write for our outputs)
    BUCKET_INPUT = "thermal-data"       # Written by ingestion, read by us
    BUCKET_OUTPUT = "thermal-processed" # Written by us
    BUCKET_RESULTS = "thermal-results"  # JSON summaries

    # ── Redis ────────────────────────────────────────────
    REDIS_HOST = os.getenv("REDIS_HOST", "redis")
    REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))

    # ── Monitoring ───────────────────────────────────────
    PROMETHEUS_PORT = int(os.getenv("PROMETHEUS_PORT", "8092"))

    # ── Processing ───────────────────────────────────────
    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

    @classmethod
    def get_minio_client(cls):
        """Return a configured MinIO client."""
        from minio import Minio
        if not cls.MINIO_ACCESS_KEY or not cls.MINIO_SECRET_KEY:
            raise EnvironmentError(
                "MINIO_ACCESS_KEY and MINIO_SECRET_KEY must be set."
            )
        return Minio(
            cls.MINIO_ENDPOINT,
            access_key=cls.MINIO_ACCESS_KEY,
            secret_key=cls.MINIO_SECRET_KEY,
            secure=False,
        )

    @classmethod
    def validate(cls):
        """Raise if critical config is missing."""
        missing = []
        if not cls.MINIO_ACCESS_KEY:
            missing.append("MINIO_ACCESS_KEY")
        if not cls.MINIO_SECRET_KEY:
            missing.append("MINIO_SECRET_KEY")
        if missing:
            raise EnvironmentError(
                f"[thermal] Missing required env vars: {missing}"
            )
        logger.info("[thermal] Configuration validated.")
