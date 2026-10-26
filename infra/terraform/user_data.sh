#!/bin/bash
# First boot only. Docker + compose, swap, the container metadata block and the
# daily timers. Deploys arrive later via SSM (scripts/deploy.sh).
set -euxo pipefail
dnf install -y docker git jq
systemctl enable --now docker
mkdir -p /usr/local/lib/docker/cli-plugins
ARCH=$(uname -m); [ "$ARCH" = "aarch64" ] && ARCH=aarch64 || ARCH=x86_64
curl -fsSL "https://github.com/docker/compose/releases/download/v2.29.7/docker-compose-linux-$ARCH" \
  -o /usr/local/lib/docker/cli-plugins/docker-compose
chmod +x /usr/local/lib/docker/cli-plugins/docker-compose

# Swap: a large scene must slow the box down, not get a worker OOM-killed
if [ ! -f /swapfile ]; then
  fallocate -l ${swap_gb}G /swapfile && chmod 600 /swapfile && mkswap /swapfile
  echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi
swapon -a || true
sysctl -w vm.swappiness=10 && echo 'vm.swappiness=10' > /etc/sysctl.d/90-hydranet.conf

mkdir -p /opt/hydranet /etc/letsencrypt
cat > /opt/hydranet/.bootstrap <<BOOT
ENV=${env}
S3_REGION=${region}
DOMAIN=${domain}
ORIGIN_HOST=${origin_host}
LE_EMAIL=${le_email}
BOOT

# Containers that need no AWS access (nginx, Kafka, Postgres, Redis: fixed IPs in
# 10.88.0.0/24, see docker-compose.aws.yml) must not reach the instance metadata
# service, which hands out the instance role's credentials.
cat > /etc/systemd/system/hydranet-imds-block.service <<'UNIT'
[Unit]
Description=Block instance metadata for HydraNet containers without AWS needs
After=docker.service
Requires=docker.service
[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/bin/sh -c 'iptables -C DOCKER-USER -s 10.88.0.0/24 -d 169.254.169.254/32 -j DROP 2>/dev/null || iptables -I DOCKER-USER -s 10.88.0.0/24 -d 169.254.169.254/32 -j DROP'
[Install]
WantedBy=multi-user.target
UNIT

# Daily: renew the origin certificate (Let's Encrypt) and back up Postgres to S3.
# Persistent=true: a run missed while the instance was stopped happens at the next start.
for job in cert:origin_cert backup:pg_backup; do
  name=$${job%%:*}; script=$${job##*:}
  cat > /etc/systemd/system/hydranet-$name.service <<UNIT
[Unit]
Description=HydraNet $name
After=docker.service
[Service]
Type=oneshot
ExecStart=/bin/bash /opt/hydranet/scripts/$script.sh
UNIT
  cat > /etc/systemd/system/hydranet-$name.timer <<UNIT
[Unit]
Description=HydraNet $name, daily
[Timer]
OnCalendar=*-*-* 18:00:00 UTC
RandomizedDelaySec=30m
Persistent=true
[Install]
WantedBy=timers.target
UNIT
done
systemctl daemon-reload
systemctl enable --now hydranet-imds-block.service hydranet-cert.timer hydranet-backup.timer
