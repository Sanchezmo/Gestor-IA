#!/usr/bin/env bash
# scripts/configure.sh
# Configuración post-instalación

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# Colores
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info()  { echo -e "${GREEN}[INFO]${NC} $*"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
log_error() { echo -e "${RED}[ERROR]${NC} $*"; }
log_step()  { echo -e "${BLUE}[STEP]${NC} $*"; }

check_root() {
    if [[ $EUID -ne 0 ]]; then
        log_error "Este script requiere root (sudo)"
        exit 1
    fi
}

main() {
    echo ""
    echo "=========================================="
    echo "  GESTOR-IA - CONFIGURACIÓN POST-INSTALACIÓN"
    echo "=========================================="
    echo ""
    
    check_root
    
    log_step "Configurando Apache para Dolibarr..."
    bash "${SCRIPT_DIR}/configure/apache.sh"
    
    log_step "Configurando Dolibarr (conf.php)..."
    bash "${SCRIPT_DIR}/configure/dolibarr.sh"
    
    log_step "Configurando servicios systemd..."
    bash "${SCRIPT_DIR}/configure/services.sh"
    
    log_step "Configurando Hermes Core..."
    bash "${SCRIPT_DIR}/configure/hermes.sh"
    
    echo ""
    log_info "Configuración base completada"
    echo ""
    log_info "Para configurar Cloudflare Tunnel:"
    echo "  1. cloudflared tunnel login"
    echo "  2. ${SCRIPT_DIR}/configure/cloudflare.sh"
    echo ""
}

main "$@"