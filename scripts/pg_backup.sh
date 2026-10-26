#!/bin/bash
# Runs ON the instance, daily (hydranet-backup.timer). Logical Postgres backup to
# s3://<bucket>/ops/pg_backups/ (encrypted; the bucket lifecycle keeps 30 days).
# Restore: aws s3 cp s3://<bucket>/ops/pg_backups/<file> - | gunzip | \
#   docker compose -f docker-compose.yml -f docker-compose.aws.yml exec -T postgres psql -U spectral hyperspectral
set -euo pipefail
cd /opt/hydranet
source .bootstrap
BUCKET=$(grep '^S3_BUCKET=' .env | cut -d= -f2)
DB_USER=$(grep '^DB_USER=' .env | cut -d= -f2)
KEY="ops/pg_backups/$(date -u +%Y-%m-%d-%H%M).sql.gz"
docker compose -f docker-compose.yml -f docker-compose.aws.yml exec -T postgres \
  pg_dump -U "$DB_USER" --no-owner hyperspectral | gzip -9 | \
  aws s3 cp - "s3://$BUCKET/$KEY" --region "$S3_REGION" --sse AES256
echo "backup written: s3://$BUCKET/$KEY"
