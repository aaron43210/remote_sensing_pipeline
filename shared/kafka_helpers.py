# =============================================================
# OWNER: AARON
# =============================================================
"""
Kafka helpers with delivery guarantees.
Replaces raw producer.send() calls across all services.
"""

import logging
from kafka import KafkaProducer

logger = logging.getLogger(__name__)


def create_reliable_producer(bootstrap_servers):
    """
    Create Kafka producer with delivery guarantees.

    Key settings:
    - acks='all': Wait for all replicas to acknowledge
    - retries=3: Retry on transient failures
    - max_in_flight_requests_per_connection=1: Ordering guarantee
    """
    return KafkaProducer(
        bootstrap_servers=bootstrap_servers,
        acks='all',
        retries=3,
        max_in_flight_requests_per_connection=1,
        request_timeout_ms=30000,
        value_serializer=lambda v: __import__('msgpack').packb(v, use_bin_type=True),
    )


def send_with_callback(producer, topic, value, key=None):
    """
    Send message with delivery callback.
    Blocks until broker acknowledges.

    Raises KafkaError on permanent failure.
    """
    future = producer.send(topic, value=value, key=key)

    try:
        record_metadata = future.get(timeout=30)
        logger.debug(
            f"Message delivered: topic={record_metadata.topic}, "
            f"partition={record_metadata.partition}, "
            f"offset={record_metadata.offset}"
        )
        return record_metadata

    except Exception as e:
        logger.error(
            f"Failed to deliver message to '{topic}': {e}",
            exc_info=True
        )
        raise
