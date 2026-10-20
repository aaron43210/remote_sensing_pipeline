#!/bin/bash
# Create every topic explicitly (auto-creation is off). Partitions bound the
# parallelism of each consumer group: scale replicas up to this number.
set -euo pipefail
B="${KAFKA_BOOTSTRAP_SERVERS:-kafka:9092}"
K=/opt/kafka/bin/kafka-topics.sh
until $K --bootstrap-server "$B" --list >/dev/null 2>&1; do echo "waiting for kafka"; sleep 2; done
create() {  # name partitions retention_ms
  $K --bootstrap-server "$B" --create --if-not-exists --topic "$1" --partitions "$2" \
     --replication-factor "${REPLICATION:-1}" --config retention.ms="$3" --config compression.type=producer
}
WEEK=604800000
create ingest.requests             12 $WEEK
create raw.hyperspectral           12 $WEEK
create raw.thermal                  6 $WEEK
create preprocessed.hyperspectral  12 $WEEK
create preprocessed.thermal         6 $WEEK
create ml.results                   6 $WEEK
create job.status                  12 $WEEK
create dlq                          1 2592000000
$K --bootstrap-server "$B" --list
