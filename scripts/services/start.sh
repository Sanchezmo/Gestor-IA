#!/usr/bin/env bash
# scripts/services/start.sh
# Iniciar todos los servicios Gestor-IA

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

start_service() {
    local service=$1
    log_info "Iniciando $service..."
    systemctl start "$service"
    sleep 1
    if systemctl is-active --quiet "$service"; then
        log_info "$service: ACTIVO"
    else
        log_error "$service: FALLÓ"
        systemctl status "$service" --no-pager
        return 1
    fi
}

main() {
    check_root
    
    echo ""
    echo "=== Iniciando Servicios Gestor-IA ==="
    echo ""
    
    # Orden de inicio: dependencias primero
    start_service mariadb
    start_service redis
    start_service ollama
    start_service php8.3-fpm
    start_service apache2
    start_service cloudflared
    start_service gestor-ia
    
    echo ""
    log_info "Todos los servicios iniciados"
    echo ""
    make status
}

main "$@"