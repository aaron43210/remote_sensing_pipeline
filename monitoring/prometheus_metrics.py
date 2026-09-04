# =============================================================
# OWNER: AARON
# =============================================================
from prometheus_client import Counter, Histogram, Gauge, start_http_server
import time
import logging

logger = logging.getLogger(__name__)

# ── Metrics ──────────────────────────────────
SCENES_PROCESSED = Counter(
    "hyperspectral_scenes_processed_total",
    "Total number of hyperspectral scenes processed",
    ["service", "status"]
)

PROCESSING_TIME = Histogram(
    "hyperspectral_processing_seconds",
    "Time to process a scene",
    ["service"],
    buckets=[0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0]
)

PREDICTION_ACCURACY = Gauge(
    "hyperspectral_model_accuracy",
    "Current model accuracy on validation set",
    ["model_type"]
)

DRIFT_SCORE = Gauge(
    "hyperspectral_drift_score",
    "Current drift score (KS statistic)",
    []
)

PHYSICS_CONSISTENCY = Gauge(
    "hyperspectral_physics_consistency",
    "Physics reconstruction error",
    []
)

KAFKA_LAG = Gauge(
    "hyperspectral_kafka_consumer_lag",
    "Kafka consumer lag per topic",
    ["topic"]
)

MEMORY_USAGE = Gauge(
    "hyperspectral_service_memory_mb",
    "Memory usage in MB",
    ["service"]
)


class MetricsCollector:
    """Collect and expose Prometheus metrics."""

    def __init__(self, port=8090):
        self.port = port

    def start(self):
        start_http_server(self.port)
        logger.info(f"Prometheus metrics server started on :{self.port}")

    def record_scene_processed(self, service, status="success"):
        SCENES_PROCESSED.labels(service=service, status=status).inc()

    def record_processing_time(self, service, seconds):
        PROCESSING_TIME.labels(service=service).observe(seconds)

    def update_accuracy(self, model_type, accuracy):
        PREDICTION_ACCURACY.labels(model_type=model_type).set(accuracy)

    def update_drift(self, drift_score):
        DRIFT_SCORE.set(drift_score)

    def update_physics_consistency(self, error):
        PHYSICS_CONSISTENCY.set(error)

    def update_kafka_lag(self, topic, lag):
        KAFKA_LAG.labels(topic=topic).set(lag)

    def update_memory(self, service, memory_mb):
        MEMORY_USAGE.labels(service=service).set(memory_mb)


def track_time(service_name, metrics_collector):
    """Decorator to track processing time."""
    def decorator(func):
        def wrapper(*args, **kwargs):
            start = time.time()
            try:
                result = func(*args, **kwargs)
                metrics_collector.record_scene_processed(service_name, "success")
                return result
            except Exception as e:
                metrics_collector.record_scene_processed(service_name, "error")
                raise e
            finally:
                elapsed = time.time() - start
                metrics_collector.record_processing_time(service_name, elapsed)
        return wrapper
    return decorator
