#!/usr/bin/env bash
# scripts/development/stop-development.sh
# Parar entorno DEVELOPMENT completo

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
    log_step "Parando Hermes..."
    if [[ -f /tmp/hermes-development.pid ]]; then
        HERMES_PID=$(cat /tmp/hermes-development.pid)
        if kill -0 "$HERMES_PID" 2>/dev/null; then
            kill "$HERMES_PID"
            log_info "Hermes parado (PID: $HERMES_PID)"
        else
            log_warn "Hermes ya no estaba corriendo"
        fi
        rm -f /tmp/hermes-development.pid
    else
        log_warn "No se encontró PID file de Hermes"
    fi
    
    # Asegurar que no quede ningún proceso en puerto 8000
    if lsof -ti:8000 >/dev/null 2>&1; then
        log_warn "Matando procesos restantes en puerto 8000..."
        lsof -ti:8000 | xargs kill -9 2>/dev/null || true
    fi
}

stop_infrastructure() {
    log_step "Parando infraestructura Docker..."
    cd "$PROJECT_ROOT"
    
    docker compose -f docker-compose.development.yml down
    
    log_info "Infraestructura parada"
}

cleanup() {
    log_step "Limpiando archivos temporales..."
    rm -f /tmp/hermes-development.log /tmp/hermes-development.pid
}

main() {
    echo ""
    echo "=== PARANDO DEVELOPMENT GESTOR-IA ==="
    echo ""
    
    stop_hermes
    stop_infrastructure
    cleanup
    
    echo ""
    log_info "DEVELOPMENT GESTOR-IA detenido correctamente"
    echo ""
}

main "$@"