"""Prometheus metrics shared by the Python workers (scraped on METRICS_PORT)."""

from prometheus_client import Counter, Gauge, Histogram, start_http_server

from .config import settings

MESSAGES = Counter("hs_messages_total", "Messages handled", ["group", "topic", "outcome"])
LATENCY = Histogram("hs_message_seconds", "Handling time per message", ["group", "topic"],
                    buckets=(0.1, 0.5, 1, 2, 5, 10, 30, 60, 120, 300, 600, 1800))
DLQ = Counter("hs_dlq_total", "Messages sent to the dead-letter topic", ["group", "topic"])
BUSY = Gauge("hs_worker_busy", "1 while a message is being processed", ["group"])
AGREEMENT = Gauge("hs_nn_classical_agreement", "Shadow agreement of HydraNet with the classical method",
                  ["task", "layer"])
PIXELS = Counter("hs_pixels_total", "Pixels predicted", ["task"])

_started = False


def serve():
    global _started
    if not _started:
        start_http_server(settings.metrics_port)
        _started = True
