#!/usr/bin/env bash
# scripts/services/restart.sh
# Reiniciar todos los servicios Gestor-IA

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$(dirname "$SCRIPT_DIR")")"

# Colores
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info()  { echo -e "${GREEN}[INFO]${NC} $*"; }
log_step()  { echo -e "${BLUE}[STEP]${NC} $*"; }

check_root() {
    if [[ $EUID -ne 0 ]]; then
        echo -e "\033[0;31m[ERROR]\033[0m Este script requiere root (sudo)"
        exit 1
    fi
}

main() {
    check_root
    
    echo ""
    echo "=== Reiniciando Servicios Gestor-IA ==="
    echo ""
    
    log_step "Deteniendo servicios..."
    bash "${SCRIPT_DIR}/stop.sh"
    
    sleep 2
    
    log_step "Iniciando servicios..."
    bash "${SCRIPT_DIR}/start.sh"
    
    echo ""
    log_info "Reinicio completado"
}

main "$@"