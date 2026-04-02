#!/usr/bin/env bash
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run this script as root."
  exit 1
fi

APP_DIR="/opt/imap-sync-service"
DATA_DIR="/var/lib/imap-sync-service"
SERVICE_USER="imap-sync"
SERVICE_GROUP="imap-sync"
UNIT_SOURCE="${APP_DIR}/imap-sync.service"
UNIT_TARGET="/etc/systemd/system/imap-sync.service"
ENV_SOURCE="${APP_DIR}/.env.example"
ENV_TARGET="${APP_DIR}/.env"

export DEBIAN_FRONTEND=noninteractive

apt update
apt install -y python3 imapsync ca-certificates

if ! getent group "${SERVICE_GROUP}" >/dev/null; then
  groupadd --system "${SERVICE_GROUP}"
fi

if ! id -u "${SERVICE_USER}" >/dev/null 2>&1; then
  useradd \
    --system \
    --gid "${SERVICE_GROUP}" \
    --home-dir "${APP_DIR}" \
    --no-create-home \
    --shell /usr/sbin/nologin \
    "${SERVICE_USER}"
fi

install -d -m 0750 -o "${SERVICE_USER}" -g "${SERVICE_GROUP}" "${DATA_DIR}"
install -d -m 0755 "${APP_DIR}"

if [[ ! -f "${ENV_TARGET}" ]]; then
  install -m 0640 -o root -g "${SERVICE_GROUP}" "${ENV_SOURCE}" "${ENV_TARGET}"
fi

install -m 0644 "${UNIT_SOURCE}" "${UNIT_TARGET}"

systemctl daemon-reload
systemctl enable imap-sync.service

echo
echo "Install complete."
echo "Next steps:"
echo "1. Edit ${ENV_TARGET}"
echo "2. Set IMAP_SYNC_API_TOKEN to a strong random value"
echo "3. Start the service: systemctl restart imap-sync.service"
