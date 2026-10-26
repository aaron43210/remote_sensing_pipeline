#!/bin/bash
# Runs ON the EC2 instance (sent by GitHub Actions through SSM, or by hand via
# Session Manager). Renders .env from SSM Parameter Store, makes sure the origin
# certificate exists, pulls the release images and restarts the stack with the
# AWS override. Usage: deploy.sh <tag>
set -euo pipefail
TAG="${1:?usage: deploy.sh <image tag>}"
cd /opt/hydranet
source .bootstrap
P="/hydranet/${ENV}"
get() {
  local v
  v=$(aws ssm get-parameter --region "$S3_REGION" --with-decryption --name "$P/$1" --query Parameter.Value --output text)
  [ "$v" = "-" ] && v=""            # Terraform stores empty lists as "-" (SSM refuses empty values)
  printf '%s' "$v"
}

for k in GOOGLE_CLIENT_ID GOOGLE_CLIENT_SECRET; do
  if [ "$(get $k)" = "CHANGE_ME" ] || [ -z "$(get $k)" ]; then
    echo "SSM $P/$k is still CHANGE_ME: set it first (see README, Deploy to AWS)" >&2
    exit 1
  fi
done
EARTHDATA=$(get EARTHDATA_TOKEN)
case "$EARTHDATA" in CHANGE_ME|unset) EARTHDATA="" ;; esac

DOMAIN_=$(get DOMAIN)
REGISTRY=$(get REGISTRY)
umask 077
cat > .env <<ENV
ENV=${ENV}
TAG=${TAG}
REGISTRY=${REGISTRY}
JWT_SECRET=$(get JWT_SECRET)
CREDENTIALS_KEY=$(get CREDENTIALS_KEY)
DB_USER=spectral
DB_PASSWORD=$(get DB_PASSWORD)
GOOGLE_CLIENT_ID=$(get GOOGLE_CLIENT_ID)
GOOGLE_CLIENT_SECRET=$(get GOOGLE_CLIENT_SECRET)
OAUTH_REDIRECT_URL=https://${DOMAIN_}/api/auth/google/callback
OIDC_ISSUER=https://accounts.google.com
ADMIN_EMAIL=$(get ADMIN_EMAIL)
ALLOWED_EMAILS=$(get ALLOWED_EMAILS)
ALLOWED_EMAIL_DOMAINS=$(get ALLOWED_EMAIL_DOMAINS)
OPEN_SIGNUP=false
ALLOWED_ORIGINS=https://${DOMAIN_}
EARTHDATA_TOKEN=${EARTHDATA}
S3_BUCKET=$(get S3_BUCKET)
S3_REGION=${S3_REGION}
S3_ENDPOINT_URL=
S3_PUBLIC_ENDPOINT_URL=
S3_FORCE_PATH_STYLE=false
S3_CREATE_BUCKET=false
ALLOW_FIXTURES=false
COOKIE_SECURE=true
TRUST_CLOUDFRONT=on
ORIGIN_SECRET=$(get ORIGIN_SECRET)
ORIGIN_HOST=${ORIGIN_HOST}
CSP_EXTRA=' upgrade-insecure-requests'
STORAGE_ORIGIN=https://$(get S3_BUCKET).s3.${S3_REGION}.amazonaws.com
ENV
chmod 600 .env

bash scripts/origin_cert.sh                     # before nginx starts: it needs the certificate
aws ecr get-login-password --region "$S3_REGION" | docker login --username AWS --password-stdin "$REGISTRY"
C="docker compose -f docker-compose.yml -f docker-compose.aws.yml"
$C pull
$C up -d --remove-orphans kafka postgres redis
$C up -d kafka-init
$C up -d --remove-orphans api_gateway orchestrator ingestion preprocessing preprocessing-thermal ml_inference event_sink frontend
$C run --rm tools

# Through nginx exactly as CloudFront arrives: HTTPS, the site's Host name, the secret header.
SECRET=$(grep '^ORIGIN_SECRET=' .env | cut -d= -f2)
for _ in $(seq 1 30); do
  if curl -fsS --resolve "${DOMAIN_}:443:127.0.0.1" -H "X-Origin-Verify: ${SECRET}" \
      "https://${DOMAIN_}/api/config" >/dev/null; then
    echo "healthy: ${TAG}"
    exit 0
  fi
  sleep 5
done
echo "deploy ${TAG}: health check failed" >&2; $C ps; exit 1
