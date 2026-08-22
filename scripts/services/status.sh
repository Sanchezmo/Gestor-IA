#!/usr/bin/env bash
# scripts/services/status.sh
# Ver estado de todos los servicios Gestor-IA

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$(dirname "$SCRIPT_DIR")")"

# Colores
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info()  { echo -e "${GREEN}[OK]${NC} $*"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
log_error() { echo -e "${RED}[FAIL]${NC} $*"; }
log_step()  { echo -e "${BLUE}[INFO]${NC} $*"; }

check_service() {
    local service=$1
    local description=$2
    
    if systemctl is-active --quiet "$service"; then
        local status=$(systemctl show -p ActiveState,SubState,MainPID "$service" | sed 's/.*=//' | tr '\n' ' ')
        log_info "$service ($description): ACTIVO [$status]"
        return 0
    else
        local failed=$(systemctl show -p ActiveState,SubState,Result "$service" | grep -E "Failed|failed" || true)
        if [[ -n "$failed" ]]; then
            log_error "$service ($description): FALLÓ"
        else
            log_warn "$service ($description): INACTIVO"
        fi
        return 1
    fi
}

check_port() {
    local port=$1
    local name=$2
    
    if ss -tlnp | grep -q ":$port "; then
        local pid=$(ss -tlnp | grep ":$port " | sed 's/.*pid=\([0-9]*\).*/\1/')
        log_info "Puerto $port ($name): ESCUCHANDO (PID: $pid)"
        return 0
    else
        log_warn "Puerto $port ($name): NO ESCUCHA"
        return 1
    fi
}

check_dolibarr_instance() {
    local instance=$1
    local instance_dir="${PROJECT_ROOT}/instances/$instance"
    local config_yml="$instance_dir/config.yml"
    
    if [[ ! -f "$config_yml" ]]; then
        return 1
    fi
    
    # Extraer info
    local internal_url=$("${PROJECT_ROOT}/.venv/bin/python" -c "
import yaml
with open('$config_yml') as f:
    cfg = yaml.safe_load(f)
print(cfg['database']['internal_url'])
")
    
    echo "  Instancia: $instance"
    echo "  URL: $internal_url"
    
    # Health check
    if curl -sf -H "DOLAPIKEY: $(grep DOLIBARR_API_KEY "$instance_dir/instance.env" 2>/dev/null | cut -d= -f2)" \
        "$internal_url/api/index.php/thirdparties?limit=1" >/dev/null 2>&1; then
        log_info "  Dolibarr API: RESPONDE"
    else
        log_warn "  Dolibarr API: NO RESPONDE"
    fi
}

main() {
    echo ""
    echo "=========================================="
    echo "  ESTADO SERVICIOS GESTOR-IA"
    echo "=========================================="
    echo ""
    
    # Servicios systemd
    log_step "Servicios systemd:"
    check_service mariadb "MariaDB"
    check_service redis "Redis"
    check_service ollama "Ollama"
    check_service php8.3-fpm "PHP-FPM"
    check_service apache2 "Apache2"
    check_service cloudflared "Cloudflare Tunnel"
    check_service gestor-ia "Gestor-IA Core"
    
    echo ""
    log_step "Puertos:"
    check_port 3306 "MariaDB"
    check_port 6379 "Redis"
    check_port 11434 "Ollama"
    check_port 8000 "Gestor-IA API"
    check_port 8081 "Dolibarr (instancia 1)"
    
    # Verificar puertos Dolibarr por instancia
    for instance_dir in "${PROJECT_ROOT}/instances"/*/; do
        if [[ -f "${instance_dir}config.yml" ]]; then
            instance=$(basename "$instance_dir")
            port=$("${PROJECT_ROOT}/.venv/bin/python" -c "
import yaml
with open('${instance_dir}config.yml') as f:
    cfg = yaml.safe_load(f)
print(cfg['dolibarr_apache_port'])
")
            check_port "$port" "Dolibarr ($instance)"
        fi
    done
    
    echo ""
    log_step "Instancias Dolibarr:"
    for instance_dir in "${PROJECT_ROOT}/instances"/*/; do
        if [[ -f "${instance_dir}config.yml" ]]; then
            instance=$(basename "$instance_dir")
            check_dolibarr_instance "$instance"
        fi
    done
    
    echo ""
    log_step "Cloudflare Tunnel:"
    if systemctl is-active --quiet cloudflared; then
        journalctl -u cloudflared -n 5 --no-pager | tail -5
    else
        log_warn "cloudflared no está activo"
    fi
    
    echo ""
    echo "=========================================="
}

main "$@"