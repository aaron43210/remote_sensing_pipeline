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
# Own domain: CloudFront -> server over HTTPS (:443, Let's Encrypt). No domain (*.cloudfront.net):
# no certificate can be issued for the server, so CloudFront -> server is HTTP (:80), still locked
# to CloudFront by the security group and the secret header.
ORIGIN_HOST=$(get ORIGIN_HOST)                  # SSM is the truth (.bootstrap is written once, at first boot)
sed -i -e "s|^DOMAIN=.*|DOMAIN=${DOMAIN_}|" -e "s|^ORIGIN_HOST=.*|ORIGIN_HOST=${ORIGIN_HOST}|" .bootstrap   # for origin_cert.sh
if [ -n "$ORIGIN_HOST" ]; then
  FRONTEND_PORT=443; NGINX_TEMPLATE_DIR=/etc/nginx/templates-aws
else
  FRONTEND_PORT=80; NGINX_TEMPLATE_DIR=/etc/nginx/templates
fi
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
FRONTEND_PORT=${FRONTEND_PORT}
NGINX_TEMPLATE_DIR=${NGINX_TEMPLATE_DIR}
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

# Daily EMIT coverage refresh (Dagster does this locally; it does not run on AWS). Idempotent.
cat > /etc/systemd/system/hydranet-catalog.service <<UNIT
[Unit]
Description=HydraNet EMIT coverage refresh
After=docker.service
[Service]
Type=oneshot
WorkingDirectory=/opt/hydranet
ExecStart=/bin/sh -c '$C run --rm tools python scripts/refresh_catalog.py'
UNIT
cat > /etc/systemd/system/hydranet-catalog.timer <<UNIT
[Unit]
Description=HydraNet EMIT coverage refresh, daily
[Timer]
OnCalendar=*-*-* 17:30:00 UTC
Persistent=true
[Install]
WantedBy=timers.target
UNIT
systemctl daemon-reload && systemctl enable --now hydranet-catalog.timer >/dev/null

# Through nginx exactly as CloudFront arrives (Host name + secret header). Healthy = the API answers
# AND a model is active: without one every map job fails, so the deploy must fail too.
SECRET=$(grep '^ORIGIN_SECRET=' .env | cut -d= -f2)
if [ "$FRONTEND_PORT" = "443" ]; then
  probe() { curl -fsS --resolve "${DOMAIN_}:443:127.0.0.1" -H "X-Origin-Verify: ${SECRET}" "https://${DOMAIN_}$1"; }
else
  probe() { curl -fsS -H "Host: ${DOMAIN_}" -H "X-Origin-Verify: ${SECRET}" "http://127.0.0.1$1"; }
fi
for _ in $(seq 1 30); do
  if probe /api/config >/dev/null && probe /api/model | grep -q '"active":true'; then
    echo "healthy: ${TAG}, model $(probe /api/model | grep -o '"version":"[^"]*"' | head -1)"
    exit 0
  fi
  sleep 5
done
echo "deploy ${TAG}: health check failed (API down or no active model)" >&2; $C ps; exit 1
