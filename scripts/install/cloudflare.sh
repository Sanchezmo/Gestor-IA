#!/usr/bin/env bash
# scripts/install/cloudflare.sh
# Instalar cloudflared nativo

set -euo pipefail

# Colores
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

log_info()  { echo -e "${GREEN}[INFO]${NC} $*"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
log_error() { echo -e "${RED}[ERROR]${NC} $*"; }

# Instalar cloudflared
if ! command -v cloudflared &> /dev/null; then
    log_info "Instalando cloudflared..."
    cd /tmp
    wget -q https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb
    dpkg -i cloudflared-linux-amd64.deb
    rm cloudflared-linux-amd64.deb
else
    log_info "cloudflared ya instalado"
fi

# Crear usuario cloudflared
id -u cloudflared &>/dev/null || useradd -r -s /bin/false -d /etc/cloudflared cloudflared
mkdir -p /etc/cloudflared
chown cloudflared:cloudflared /etc/cloudflared

# Crear systemd service
cat > /etc/systemd/system/cloudflared.service << 'EOF'
[Unit]
Description=Cloudflare Tunnel
After=network.target

[Service]
Type=notify
User=cloudflared
Group=cloudflared
ExecStart=/usr/local/bin/cloudflared tunnel --config /etc/cloudflared/config.yml run
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable cloudflared

log_info "cloudflared instalado"
log_warn "Configurar túnel manualmente:"
echo "  1. cloudflared tunnel login"
echo "  2. cloudflared tunnel create gestor-ia"
echo "  3. Editar /etc/cloudflared/config.yml"
echo "  4. systemctl start cloudflared"