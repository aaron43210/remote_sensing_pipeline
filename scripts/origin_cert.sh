#!/bin/bash
# Runs ON the instance (deploy.sh, and daily by hydranet-cert.timer). Issues or
# renews the origin's TLS certificate (Let's Encrypt, DNS-01 through Route 53 with
# the instance role; no port 80 needed), covering the site domain and origin.<domain>,
# then reloads nginx. Idempotent: renews only within 30 days of expiry.
set -euo pipefail
source /opt/hydranet/.bootstrap
if [ -z "${ORIGIN_HOST:-}" ] || [ "${ORIGIN_HOST}" = "-" ]; then
  echo "No custom domain configured; skipping origin TLS certificate."
  exit 0
fi
docker run --rm -v /etc/letsencrypt:/etc/letsencrypt certbot/dns-route53:v5.1.0 certonly \
  --dns-route53 --non-interactive --agree-tos -m "$LE_EMAIL" --keep-until-expiring \
  --cert-name "$ORIGIN_HOST" -d "$ORIGIN_HOST" -d "$DOMAIN"
cd /opt/hydranet
if docker compose -f docker-compose.yml -f docker-compose.aws.yml ps --status running frontend 2>/dev/null | grep -q frontend; then
  docker compose -f docker-compose.yml -f docker-compose.aws.yml exec -T frontend nginx -s reload
fi
