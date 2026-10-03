# =============================================================
# OWNER: ANANTAHANARAYANAN
# =============================================================
"""
Preprocessing service: consume 'raw-data', publish 'preprocessed-multiband'.

Kafka and lifecycle only -- the science is in pipeline.py.
"""

import logging
import sys
import time

import config
import message
import pipeline
import storage
from lazy import SceneRejected

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [preprocessing] %(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def handle(fs, producer, source):
    """Preprocess one scene and publish the result."""
    scene_id = source["scene_id"]
    storage.ensure_bucket(fs, config.BUCKET_OUT)
    out = pipeline.run_scene(
        source,
        storage.input_store(fs, source["zarr_path"]),
        storage.output_store(fs, scene_id),
        storage.npy_writer(fs, scene_id) if config.WRITE_NPY else None,
    )
    producer.send(config.TOPIC_OUT, out).get(timeout=30)   # wait for the ack
    logger.info("Published %s: shape %s, interpolated %s", scene_id,
                out["shape"], out["interpolated_bands"])


def run():
    from kafka import KafkaConsumer, KafkaProducer

    consumer = KafkaConsumer(
        config.TOPIC_IN, bootstrap_servers=config.KAFKA_SERVERS,
        group_id=config.CONSUMER_GROUP, value_deserializer=message.decode,
        auto_offset_reset="earliest", enable_auto_commit=False,
        max_poll_records=1, max_poll_interval_ms=600_000)
    producer = KafkaProducer(
        bootstrap_servers=config.KAFKA_SERVERS, value_serializer=message.encode,
        acks="all", retries=5)
    fs = storage.filesystem()

    logger.info("Listening on '%s'", config.TOPIC_IN)
    for msg in consumer:
        try:
            handle(fs, producer, msg.value)
        except SceneRejected as exc:          # bad input, not a bug
            logger.error("Scene rejected: %s", exc)
        except Exception:
            logger.error("Failed on %r", msg.value, exc_info=True)
        # Committed on every outcome: a scene that failed twice will fail a
        # third time, and replaying it forever would block every other user.
        consumer.commit()


if __name__ == "__main__":
    # Kafka is often not ready when this container starts.
    for attempt in range(1, 11):
        try:
            run()
            break
        except Exception as exc:
            logger.warning("Startup attempt %d/10 failed: %s", attempt, exc)
            time.sleep(5 * attempt)
    else:
        sys.exit(1)
