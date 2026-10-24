#!/bin/bash
# Runs ON the EC2 instance (sent by GitHub Actions through SSM, or by hand via
# Session Manager). Renders .env from SSM Parameter Store, pulls the release
# images and restarts the stack with the AWS override. Usage: deploy.sh <tag>
set -euo pipefail
TAG="${1:?usage: deploy.sh <image tag>}"
cd /opt/hydranet
source .bootstrap
P="/hydranet/${ENV}"
get() { aws ssm get-parameter --region "$S3_REGION" --with-decryption --name "$P/$1" --query Parameter.Value --output text; }
REGISTRY=$(get REGISTRY)
cat > .env <<ENV
ENV=${ENV}
TAG=${TAG}
REGISTRY=${REGISTRY}
JWT_SECRET=$(get JWT_SECRET)
CREDENTIALS_KEY=$(get CREDENTIALS_KEY)
DB_USER=spectral
DB_PASSWORD=$(get DB_PASSWORD)
ADMIN_EMAIL=$(get ADMIN_EMAIL)
ADMIN_PASSWORD=$(get ADMIN_PASSWORD)
EARTHDATA_TOKEN=$(get EARTHDATA_TOKEN | sed 's/^unset$//')
S3_BUCKET=$(get S3_BUCKET)
S3_REGION=${S3_REGION}
S3_ENDPOINT_URL=
S3_PUBLIC_ENDPOINT_URL=
S3_FORCE_PATH_STYLE=false
S3_CREATE_BUCKET=false
PUBLIC_ORIGIN=$(get PUBLIC_ORIGIN)
ALLOW_FIXTURES=false
COOKIE_SECURE=true
TRUST_CLOUDFRONT=on
CSP_EXTRA=' upgrade-insecure-requests'
STORAGE_ORIGIN=https://$(get S3_BUCKET).s3.${S3_REGION}.amazonaws.com
ENV
chmod 600 .env
aws ecr get-login-password --region "$S3_REGION" | docker login --username AWS --password-stdin "$REGISTRY"
C="docker compose -f docker-compose.yml -f docker-compose.aws.yml"
$C pull
$C up -d --remove-orphans kafka postgres redis
$C up -d kafka-init
$C up -d --remove-orphans api_gateway orchestrator ingestion preprocessing preprocessing-thermal ml_inference event_sink frontend
$C run --rm tools
for i in $(seq 1 30); do curl -fsS http://localhost/api/config >/dev/null && echo "healthy: ${TAG}" && exit 0; sleep 5; done
echo "deploy ${TAG}: health check failed" >&2; $C ps; exit 1
