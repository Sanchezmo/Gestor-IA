#!/usr/bin/env bash
# scripts/install/hermes.sh
# Preparar Hermes Core (systemd service)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$(dirname "$SCRIPT_DIR")")"

# Colores
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

log_info()  { echo -e "${GREEN}[INFO]${NC} $*"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
log_error() { echo -e "${RED}[ERROR]${NC} $*"; }

# Crear usuario gestor-ia
id -u gestor-ia &>/dev/null || useradd -r -s /bin/false -d /var/lib/gestor-ia gestor-ia
mkdir -p /var/lib/gestor-ia /var/log/gestor-ia /var/backups/gestor-ia
chown -R gestor-ia:gestor-ia /var/lib/gestor-ia /var/log/gestor-ia /var/backups/gestor-ia

# Copiar systemd service
cp "${PROJECT_ROOT}/config/systemd/gestor-ia.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable gestor-ia

log_info "Hermes Core systemd service instalado"
log_warn "Requiere .env configurado y make configure ejecutado"