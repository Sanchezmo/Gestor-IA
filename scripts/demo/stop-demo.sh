#!/usr/bin/env bash
# scripts/demo/stop-demo.sh
# Detener entorno de demo completo

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

stop_hermes() {
    log_step "Deteniendo Hermes Core..."
    if [[ -f /tmp/hermes-demo.pid ]]; then
        HERMES_PID=$(cat /tmp/hermes-demo.pid)
        if kill -0 $HERMES_PID 2>/dev/null; then
            kill $HERMES_PID
            log_info "Hermes detenido (PID: $HERMES_PID)"
        else
            log_warn "Hermes no estaba corriendo"
        fi
        rm -f /tmp/hermes-demo.pid
    else
        log_warn "No se encontró PID de Hermes"
    fi
}

stop_infrastructure() {
    log_step "Deteniendo infraestructura Docker..."
    cd "$PROJECT_ROOT"
    docker compose -f docker-compose.demo.yml down
    log_info "Infraestructura detenida"
}

cleanup() {
    log_step "Limpiando archivos temporales..."
    rm -f /tmp/hermes-demo.log /tmp/hermes-demo.pid
    log_info "Limpieza completada"
}

main() {
    echo ""
    echo "=== DETENIENDO DEMO GESTOR-IA ==="
    echo ""
    
    stop_hermes
    stop_infrastructure
    cleanup
    
    echo ""
    log_info "Demo detenido completamente"
    echo ""
}

main "$@"