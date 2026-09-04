# =============================================================
# OWNER: AARON
# =============================================================
"""
Centralized configuration loader.
All services import credentials from here instead of hardcoding.
"""

import os
import logging

logger = logging.getLogger(__name__)

class Settings:
    """Load all configuration from environment variables."""

    # Kafka
    KAFKA_BOOTSTRAP_SERVERS = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "kafka:29092")

    # MinIO
    MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "minio:9000")
    MINIO_ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY")
    MINIO_SECRET_KEY = os.getenv("MINIO_SECRET_KEY")

    # Redis
    REDIS_HOST = os.getenv("REDIS_HOST", "redis")
    REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))

    # Postgres
    DATABASE_URL = os.getenv("DATABASE_URL")

    # Security
    JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY")

    # Monitoring
    SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL")
    PROMETHEUS_PORT = int(os.getenv("PROMETHEUS_PORT", "8090"))

    @classmethod
    def validate_required(cls):
        """Fail fast if critical env vars are missing."""
        required = {
            "MINIO_ACCESS_KEY": cls.MINIO_ACCESS_KEY,
            "MINIO_SECRET_KEY": cls.MINIO_SECRET_KEY,
        }
        missing = [k for k, v in required.items() if not v]
        if missing:
            raise EnvironmentError(
                f"Missing required environment variables: {missing}"
            )
        logger.info("Configuration validated successfully")

    @classmethod
    def get_minio_client(cls):
        """Return a configured MinIO client."""
        from minio import Minio
        return Minio(
            cls.MINIO_ENDPOINT,
            access_key=cls.MINIO_ACCESS_KEY,
            secret_key=cls.MINIO_SECRET_KEY,
            secure=False,
        )


# Validate on import
try:
    Settings.validate_required()
except EnvironmentError as e:
    logger.warning(f"Config validation: {e}")
