#!/usr/bin/env bash
# scripts/development/stop-development.sh
# Parar entorno DEVELOPMENT nativo (systemd)
# NOTA: Este script es un wrapper. La interfaz principal es: make dev-stop

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$(dirname "$SCRIPT_DIR")")"

# Colores
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info()  { echo -e "${GREEN}[INFO]${NC} $*"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
log_error() { echo -e "${RED}[ERROR]${NC} $*"; }
log_step()  { echo -e "${BLUE}[STEP]${NC} $*"; }

check_sudo() {
    if ! sudo -n true 2>/dev/null; then
        log_error "Se requiere sudo sin password para gestionar servicios systemd."
        log_error "Instala el sudoers: sudo install -o root -g root -m 0440 .local/gestor-ia-development.sudoers /etc/sudoers.d/gestor-ia-development"
        exit 1
    fi
}

stop_services() {
    log_step "Parando servicios nativos..."
    
    # Parar Hermes primero
    sudo -n systemctl stop hermes-development
    
    # Parar infraestructura (orden inverso)
    sudo -n systemctl stop ollama cloudflared apache2 redis-server mariadb 2>/dev/null || true
    
    log_info "Servicios nativos parados"
}

main() {
    echo ""
    echo "=== PARANDO DEVELOPMENT GESTOR-IA (NATIVO) ==="
    echo ""
    
    check_sudo
    stop_services
    
    echo ""
    log_info "DEVELOPMENT GESTOR-IA detenido correctamente"
    echo ""
}

main "$@"