#!/usr/bin/env bash
# scripts/check.sh
# Verificación profunda del entorno Gestor-IA

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
log_step()  { echo -e "${BLUE}[STEP]${NC} $*"; }

ERRORS=0
WARNINGS=0

check() {
    local cmd=$1
    local desc=$2
    if eval "$cmd" >/dev/null 2>&1; then
        log_info "$desc"
        return 0
    else
        log_error "$desc"
        ((ERRORS++))
        return 1
    fi
}

warn() {
    local cmd=$1
    local desc=$2
    if eval "$cmd" >/dev/null 2>&1; then
        log_info "$desc"
        return 0
    else
        log_warn "$desc"
        ((WARNINGS++))
        return 1
    fi
}

main() {
    echo ""
    echo "=========================================="
    echo "  VERIFICACIÓN PROFUNDA GESTOR-IA"
    echo "=========================================="
    echo ""
    
    # 1. Sistema
    log_step "Sistema base:"
    check "command -v python3" "Python3 instalado"
    check "command -v mysql" "MariaDB client instalado"
    check "command -v redis-cli" "Redis client instalado"
    check "command -v apache2ctl" "Apache2 instalado"
    check "command -v php" "PHP instalado"
    check "command -v ollama" "Ollama instalado"
    check "command -v cloudflared" "Cloudflared instalado"
    check "command -v docker" "Docker instalado (para tests)"
    check "command -v jq" "jq instalado"
    
    # 2. Python environment
    log_step "Entorno Python:"
    if [[ -d "${PROJECT_ROOT}/.venv" ]]; then
        log_info "Virtualenv existe"
        "${PROJECT_ROOT}/.venv/bin/python" -c "import fastapi, pydantic, httpx, sqlalchemy, redis, structlog, yaml" 2>/dev/null && \
            log_info "Dependencias core instaladas" || log_error "Dependencias core FALTANTES"
    else
        log_error "Virtualenv no encontrado (ejecutar make install-python)"
    fi
    
    # 3. Configuración
    log_step "Configuración:"
    check "[[ -f ${PROJECT_ROOT}/.env ]]" "Archivo .env existe"
    
    # 4. Servicios
    log_step "Servicios systemd:"
    for svc in mariadb redis ollama php8.3-fpm apache2 cloudflared gestor-ia; do
        if systemctl is-active --quiet "$svc"; then
            log_info "$svc: ACTIVO"
        else
            log_warn "$svc: INACTIVO"
            ((WARNINGS++))
        fi
    done
    
    # 5. Conectividad BD
    log_step "Conectividad bases de datos:"
    if mysql -u root -p"${MARIADB_ROOT_PASSWORD}" -e "SELECT 1" >/dev/null 2>&1; then
        log_info "MariaDB: CONEXIÓN OK"
    else
        log_error "MariaDB: CONEXIÓN FALLÓ"
    fi
    
    if redis-cli -a "${REDIS_PASSWORD}" ping >/dev/null 2>&1; then
        log_info "Redis: CONEXIÓN OK"
    else
        log_error "Redis: CONEXIÓN FALLÓ"
    fi
    
    # 6. Ollama
    log_step "Ollama:"
    if curl -sf http://127.0.0.1:11434/api/tags >/dev/null; then
        log_info "Ollama API: RESPONDE"
        curl -sf http://127.0.0.1:11434/api/tags | jq '.models[]?.name' 2>/dev/null | while read model; do
            log_info "  Modelo: $model"
        done
    else
        log_error "Ollama API: NO RESPONDE"
    fi
    
    # 7. Apache
    log_step "Apache:"
    if apache2ctl configtest >/dev/null 2>&1; then
        log_info "Configuración Apache: VÁLIDA"
    else
        log_error "Configuración Apache: INVÁLIDA"
        apache2ctl configtest
    fi
    
    # 8. Instancias
    log_step "Instancias configuradas:"
    for instance_dir in "${PROJECT_ROOT}/instances"/*/; do
        if [[ -f "${instance_dir}config.yml" ]]; then
            instance=$(basename "$instance_dir")
            log_info "Instancia encontrada: $instance"
            
            # Verificar config
            if "${PROJECT_ROOT}/.venv/bin/python" -c "
import yaml
with open('${instance_dir}config.yml') as f:
    yaml.safe_load(f)
print('OK')
" >/dev/null 2>&1; then
                log_info "  config.yml: VÁLIDO"
            else
                log_error "  config.yml: INVÁLIDO"
            fi
            
            # Verificar instance.env
            if [[ -f "${instance_dir}instance.env" ]]; then
                log_info "  instance.env: EXISTE"
            else
                log_warn "  instance.env: FALTA"
            fi
        fi
    done
    
    # 9. Puertos
    log_step "Puertos en escucha:"
    for port in 3306 6379 11434 8000 8081 8082; do
        if ss -tlnp | grep -q ":$port "; then
            log_info "Puerto $port: ESCUCHANDO"
        else
            log_warn "Puerto $port: NO ESCUCHA"
        fi
    done
    
    # 10. Cloudflare Tunnel
    log_step "Cloudflare Tunnel:"
    if systemctl is-active --quiet cloudflared; then
        log_info "cloudflared: ACTIVO"
        # Verificar túnel
        if journalctl -u cloudflared -n 20 --no-pager | grep -q "Connection registered\|Registered tunnel"; then
            log_info "Túnel: REGISTRADO"
        else
            log_warn "Túnel: SIN REGISTRAR (ejecutar cloudflared tunnel login)"
        fi
    else
        log_warn "cloudflared: INACTIVO"
    fi
    
    # Resumen
    echo ""
    echo "=========================================="
    echo "  RESUMEN"
    echo "=========================================="
    if [[ $ERRORS -eq 0 && $WARNINGS -eq 0 ]]; then
        log_info "TODO OK - Entorno listo para producción"
    elif [[ $ERRORS -eq 0 ]]; then
        log_warn "$WARNINGS advertencias - Revisar elementos opcionales"
    else
        log_error "$ERRORS errores críticos - CORREGIR ANTES DE PRODUCIR"
        echo "Advertencias: $WARNINGS"
    fi
    echo ""
    
    exit $ERRORS
}

main "$@"