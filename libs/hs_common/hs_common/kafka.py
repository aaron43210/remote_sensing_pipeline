"""
Kafka for the Python workers: at-least-once consumption with idempotency,
bounded retries and a dead-letter topic.

    worker = Worker("preprocessing", ["raw.hyperspectral"], handle)
    worker.run()

`handle(topic, msg_dict)` does the work. Offsets are committed only after it
returns (or after the message has been dead-lettered), so a crash re-delivers
the message. A Redis key per (consumer group, event_id) makes re-delivery of
an already-finished message a no-op. A handler raises `Permanent` for input
that will never succeed (no point retrying), anything else is retried.
"""

import json
import logging
import signal
import time

import redis
from confluent_kafka import Consumer, KafkaError, KafkaException, Producer

from . import metrics
from .config import settings
from .logs import job_id_var

log = logging.getLogger(__name__)
DLQ_TOPIC = "dlq"
DONE_TTL_S = 7 * 24 * 3600


class Permanent(Exception):
    """The input is bad; retrying will not help. Goes straight to the DLQ."""


class NoData(Permanent):
    """No imagery for the request. Reported as NO_DATA, not as an error."""


_producer = None


def producer():
    global _producer
    if _producer is None:
        _producer = Producer({"bootstrap.servers": settings.kafka_bootstrap,
                              "acks": "all", "enable.idempotence": True,
                              "linger.ms": 5, "compression.type": "zstd",
                              "message.max.bytes": 4 * 1024 * 1024,
                              "client.id": settings.service})
    return _producer


def publish(topic, msg, key=None, flush=True):
    """msg: a pydantic model or dict. Key defaults to job_id (keeps a job on one partition)."""
    body = msg.model_dump(mode="json") if hasattr(msg, "model_dump") else msg
    key = key or body.get("job_id")
    errors = []

    def cb(err, _):
        if err:
            errors.append(err)

    producer().produce(topic, json.dumps(body).encode(), key=key.encode() if key else None, on_delivery=cb)
    if flush:
        producer().flush(30)
        if errors:
            raise KafkaException(errors[0])


class Worker:
    def __init__(self, group, topics, handle, max_retries=None):
        self.group, self.topics, self.handle = group, topics, handle
        self.max_retries = settings.kafka_max_retries if max_retries is None else max_retries
        self.redis = redis.Redis.from_url(settings.redis_url, socket_timeout=5)
        self.consumer = Consumer({
            "bootstrap.servers": settings.kafka_bootstrap, "group.id": group,
            "enable.auto.commit": False, "auto.offset.reset": "earliest",
            "max.poll.interval.ms": settings.kafka_max_poll_interval_ms,
            "session.timeout.ms": 45000, "partition.assignment.strategy": "cooperative-sticky",
            "client.id": f"{settings.service}"})
        self.running = True
        signal.signal(signal.SIGTERM, self._stop)
        signal.signal(signal.SIGINT, self._stop)

    def _stop(self, *_):
        log.info("shutdown requested; finishing the current message")
        self.running = False

    def _done_key(self, event_id):
        return f"done:{self.group}:{event_id}"

    def _dead_letter(self, topic, raw, err):
        publish(DLQ_TOPIC, {"job_id": (raw or {}).get("job_id", "unknown"), "topic": topic,
                            "group": self.group, "error": str(err)[:4000], "message": raw,
                            "ts": time.time()})
        metrics.DLQ.labels(self.group, topic).inc()

    def _process(self, m):
        topic = m.topic()
        try:
            raw = json.loads(m.value())
        except Exception as e:                  # unparsable: nothing to retry
            self._dead_letter(topic, {"raw": m.value()[:2000].decode("utf-8", "replace")}, e)
            return
        event_id = raw.get("event_id")
        job_id_var.set(raw.get("job_id"))
        if event_id and self.redis.exists(self._done_key(event_id)):
            log.info("duplicate delivery of %s, skipped", event_id)
            return
        for attempt in range(self.max_retries + 1):
            t0 = time.time()
            try:
                self.handle(topic, raw)
                metrics.MESSAGES.labels(self.group, topic, "ok").inc()
                metrics.LATENCY.labels(self.group, topic).observe(time.time() - t0)
                if event_id:
                    self.redis.set(self._done_key(event_id), 1, ex=DONE_TTL_S)
                return
            except Permanent as e:
                metrics.MESSAGES.labels(self.group, topic, "permanent").inc()
                log.warning("permanent failure: %s", e)
                if not isinstance(e, NoData):
                    self._dead_letter(topic, raw, e)
                return
            except Exception as e:
                metrics.MESSAGES.labels(self.group, topic, "error").inc()
                if attempt >= self.max_retries:
                    log.exception("giving up after %d attempts", attempt + 1)
                    self._dead_letter(topic, raw, e)
                    self.on_give_up(raw, e)
                    return
                delay = min(60, 2 ** attempt * 2)
                log.warning("attempt %d failed (%s); retrying in %ss", attempt + 1, e, delay)
                time.sleep(delay)

    def on_give_up(self, raw, err):
        """Hook: services report the final error to job.status."""

    def run(self):
        metrics.serve()
        self.consumer.subscribe(self.topics)
        log.info("%s consuming %s", self.group, self.topics)
        try:
            while self.running:
                m = self.consumer.poll(settings.kafka_poll_timeout_s)
                if m is None:
                    continue
                if m.error():
                    if m.error().code() != KafkaError._PARTITION_EOF:
                        log.error("kafka error: %s", m.error())
                    continue
                metrics.BUSY.labels(self.group).set(1)
                try:
                    self._process(m)
                finally:
                    metrics.BUSY.labels(self.group).set(0)
                    job_id_var.set(None)
                self.consumer.commit(message=m, asynchronous=False)
        finally:
            self.consumer.close()
            producer().flush(10)
