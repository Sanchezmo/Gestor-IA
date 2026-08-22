#!/usr/bin/env bash
# scripts/install/ollama.sh
# Instalar Ollama nativo

set -euo pipefail

# Colores
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

log_info()  { echo -e "${GREEN}[INFO]${NC} $*"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
log_error() { echo -e "${RED}[ERROR]${NC} $*"; }

# Instalar Ollama
if ! command -v ollama &> /dev/null; then
    log_info "Instalando Ollama..."
    curl -fsSL https://ollama.com/install.sh | sh
else
    log_info "Ollama ya instalado"
fi

# Configurar systemd service si no existe
if [[ ! -f /etc/systemd/system/ollama.service ]]; then
    cat > /etc/systemd/system/ollama.service << 'EOF'
[Unit]
Description=Ollama Service
After=network-online.target

[Service]
Type=notify
User=ollama
Group=ollama
ExecStart=/usr/local/bin/ollama serve
Restart=always
RestartSec=3
Environment="OLLAMA_HOST=127.0.0.1:11434"
Environment="OLLAMA_ORIGINS=*"
LimitNOFILE=65536

[Install]
WantedBy=multi-user.target
EOF

    # Crear usuario ollama si no existe
    id -u ollama &>/dev/null || useradd -r -s /bin/false -d /usr/share/ollama ollama
    mkdir -p /usr/share/ollama
    chown ollama:ollama /usr/share/ollama
fi

systemctl daemon-reload
systemctl enable ollama
systemctl restart ollama

# Esperar a que Ollama esté listo
sleep 5

# Descargar modelo por defecto
log_info "Descargando modelo por defecto (qwen3.5:4b)..."
ollama pull qwen3.5:4b || log_warn "No se pudo descargar modelo (reintentar manualmente: ollama pull qwen3.5:4b)"

log_info "Ollama instalado y configurado"