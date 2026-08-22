#!/usr/bin/env bash
# scripts/services/stop.sh
# Detener todos los servicios Gestor-IA

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

check_root() {
    if [[ $EUID -ne 0 ]]; then
        log_error "Este script requiere root (sudo)"
        exit 1
    fi
}

stop_service() {
    local service=$1
    log_info "Deteniendo $service..."
    systemctl stop "$service"
    sleep 1
    if systemctl is-active --quiet "$service"; then
        log_warn "$service: SIGUE ACTIVO (forzando...)"
        systemctl kill "$service"
        sleep 1
    else
        log_info "$service: DETENIDO"
    fi
}

main() {
    check_root
    
    echo ""
    echo "=== Deteniendo Servicios Gestor-IA ==="
    echo ""
    
    # Orden inverso: apps primero, dependencias al final
    stop_service gestor-ia
    stop_service cloudflared
    stop_service apache2
    stop_service php8.3-fpm
    stop_service ollama
    stop_service redis
    stop_service mariadb
    
    echo ""
    log_info "Todos los servicios detenidos"
}

main "$@"