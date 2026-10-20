#!/bin/bash
# First boot: Docker + compose plugin, app directory. Deploys arrive via SSM (scripts/deploy.sh).
set -euxo pipefail
dnf install -y docker git jq
systemctl enable --now docker
mkdir -p /usr/local/lib/docker/cli-plugins
ARCH=$(uname -m); [ "$ARCH" = "aarch64" ] && ARCH=aarch64 || ARCH=x86_64
curl -fsSL "https://github.com/docker/compose/releases/download/v2.29.7/docker-compose-linux-$ARCH" \
  -o /usr/local/lib/docker/cli-plugins/docker-compose
chmod +x /usr/local/lib/docker/cli-plugins/docker-compose
mkdir -p /opt/hydranet && echo "ENV=${env}" > /opt/hydranet/.bootstrap && echo "S3_REGION=${region}" >> /opt/hydranet/.bootstrap
