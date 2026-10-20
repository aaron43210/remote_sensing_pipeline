"""
Environment contract shared by every Python service.

The same variable names work locally (MinIO) and on AWS (S3): only values change.
See .env.example at the repo root.
"""

import os
from dataclasses import dataclass, field


def _bool(name, default):
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Settings:
    env: str = field(default_factory=lambda: os.getenv("ENV", "local"))
    service: str = field(default_factory=lambda: os.getenv("SERVICE_NAME", "unknown"))

    # Object storage
    s3_bucket: str = field(default_factory=lambda: os.getenv("S3_BUCKET", "hyperspectral-local"))
    s3_endpoint_url: str = field(default_factory=lambda: os.getenv("S3_ENDPOINT_URL", ""))
    s3_region: str = field(default_factory=lambda: os.getenv("S3_REGION", "us-west-2"))
    s3_force_path_style: bool = field(default_factory=lambda: _bool("S3_FORCE_PATH_STYLE", False))
    s3_create_bucket: bool = field(default_factory=lambda: _bool("S3_CREATE_BUCKET", False))

    # Kafka
    kafka_bootstrap: str = field(default_factory=lambda: os.getenv("KAFKA_BOOTSTRAP_SERVERS", "kafka:9092"))
    kafka_max_retries: int = field(default_factory=lambda: int(os.getenv("KAFKA_MAX_RETRIES", "3")))
    kafka_poll_timeout_s: float = 1.0
    # Long jobs (a 1.8 GB download) must not be considered dead by the broker
    kafka_max_poll_interval_ms: int = field(
        default_factory=lambda: int(os.getenv("KAFKA_MAX_POLL_INTERVAL_MS", "1800000")))

    # Redis
    redis_url: str = field(default_factory=lambda: os.getenv("REDIS_URL", "redis://redis:6379/0"))

    # Observability
    metrics_port: int = field(default_factory=lambda: int(os.getenv("METRICS_PORT", "9100")))
    log_level: str = field(default_factory=lambda: os.getenv("LOG_LEVEL", "INFO"))


settings = Settings()
