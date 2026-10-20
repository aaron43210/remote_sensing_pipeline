"""
Event sink: every Kafka event -> the lake's Bronze layer as Parquet.

    bronze/events/topic=<topic>/dt=<YYYY-MM-DD>/<uuid>.parquet

Micro-batches (FLUSH_ROWS events or FLUSH_SECONDS, whichever first). Offsets
are committed only after the batch is durably in object storage, so a crash
re-writes a batch at worst (downstream dedupes on event_id); nothing is lost.
The batch pipeline compacts these files and builds the Gold analytics tables.
"""

import io
import json
import logging
import os
import signal
import time
import uuid
from collections import defaultdict
from datetime import datetime, timezone

import pyarrow as pa
import pyarrow.parquet as pq
from confluent_kafka import Consumer, KafkaError, TopicPartition

from hs_common import logs, metrics, storage
from hs_common.config import settings
from hs_common.storage import keys

log = logging.getLogger("event_sink")
TOPICS = ["ingest.requests", "raw.hyperspectral", "raw.thermal", "preprocessed.hyperspectral",
          "preprocessed.thermal", "ml.results", "job.status", "dlq"]
FLUSH_ROWS = int(os.getenv("FLUSH_ROWS", "5000"))
FLUSH_SECONDS = int(os.getenv("FLUSH_SECONDS", "60"))
SCHEMA = pa.schema([("topic", pa.string()), ("partition", pa.int32()), ("offset", pa.int64()),
                    ("kafka_ts", pa.timestamp("ms", tz="UTC")), ("event_id", pa.string()),
                    ("job_id", pa.string()), ("stage", pa.string()), ("state", pa.string()),
                    ("task", pa.string()), ("payload", pa.string())])


def row(m):
    try:
        body = json.loads(m.value())
    except Exception:
        body = {}
    ts_type, ts = m.timestamp()
    return {"topic": m.topic(), "partition": m.partition(), "offset": m.offset(),
            "kafka_ts": datetime.fromtimestamp((ts if ts > 0 else time.time() * 1000) / 1000, timezone.utc),
            "event_id": body.get("event_id"), "job_id": body.get("job_id"), "stage": body.get("stage"),
            "state": body.get("state"), "task": body.get("task"),
            "payload": m.value().decode("utf-8", "replace")}


def flush(rows, consumer):
    by_key = defaultdict(list)
    for r in rows:
        by_key[(r["topic"], r["kafka_ts"].date().isoformat())].append(r)
    for (topic, date), part in by_key.items():
        table = pa.Table.from_pylist(part, schema=SCHEMA)
        buf = io.BytesIO()
        pq.write_table(table, buf, compression="zstd")
        storage.put_bytes(keys.events(topic, date, uuid.uuid4().hex), buf.getvalue(), "application/vnd.apache.parquet")
    last = {}
    for r in rows:
        last[(r["topic"], r["partition"])] = max(last.get((r["topic"], r["partition"]), -1), r["offset"])
    consumer.commit(offsets=[TopicPartition(t, p, o + 1) for (t, p), o in last.items()], asynchronous=False)
    metrics.MESSAGES.labels("event_sink", "all", "ok").inc(len(rows))
    log.info("flushed %d events in %d files", len(rows), len(by_key))


def main():
    logs.setup()
    storage.ensure_bucket()
    metrics.serve()
    consumer = Consumer({"bootstrap.servers": settings.kafka_bootstrap, "group.id": "event-sink",
                         "enable.auto.commit": False, "auto.offset.reset": "earliest"})
    consumer.subscribe(TOPICS)
    running = [True]
    signal.signal(signal.SIGTERM, lambda *_: running.__setitem__(0, False))
    rows, started = [], time.time()
    try:
        while running[0]:
            m = consumer.poll(1.0)
            if m is not None and not m.error():
                rows.append(row(m))
            elif m is not None and m.error().code() != KafkaError._PARTITION_EOF:
                log.error("kafka: %s", m.error())
            if rows and (len(rows) >= FLUSH_ROWS or time.time() - started >= FLUSH_SECONDS):
                flush(rows, consumer)
                rows, started = [], time.time()
            elif not rows:
                started = time.time()
        if rows:
            flush(rows, consumer)
    finally:
        consumer.close()


if __name__ == "__main__":
    main()
