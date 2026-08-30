#!/usr/bin/env bash
# scripts/development/start-development.sh
# Iniciar entorno DEVELOPMENT nativo (systemd)
# NOTA: Este script es un wrapper. La interfaz principal es: make dev-start

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

start_services() {
    log_step "Iniciando servicios nativos (MariaDB, Redis, Apache, Cloudflare, Ollama, Hermes)..."
    
    # Servicios de infraestructura
    sudo -n systemctl start mariadb redis-server apache2 cloudflared ollama 2>/dev/null || true
    
    # Hermes (después de la infraestructura)
    sudo -n systemctl start hermes-development
    
    log_info "Esperando a que los servicios estén listos..."
    sleep 3
}

check_health() {
    log_step "Verificando salud de los servicios..."
    
    local all_ok=true
    
    # Verificar Hermes API
    if curl -sf http://localhost:8000/health >/dev/null 2>&1; then
        log_info "Hermes API: OK"
    else
        log_error "Hermes API: NO RESPONDE"
        all_ok=false
    fi
    
    # Verificar servicios systemd
    for svc in mariadb redis-server apache2 cloudflared ollama hermes-development; do
        if sudo -n systemctl is-active "$svc" >/dev/null 2>&1; then
            log_info "$svc: activo"
        else
            log_warn "$svc: inactivo"
            all_ok=false
        fi
    done
    
    if [[ "$all_ok" == "true" ]]; then
        log_info "Todos los servicios están saludables"
        return 0
    else
        log_warn "Algunos servicios no están listos"
        return 1
    fi
}

show_status() {
    echo ""
    echo "=========================================="
    echo "  DEVELOPMENT GESTOR-IA LISTO (native)"
    echo "=========================================="
    echo ""
    echo "Servicios nativos:"
    echo "  - Dolibarr ERP:     http://localhost:8081 (Apache + PHP nativo)"
    echo "  - Hermes API:       http://localhost:8000"
    echo "    Healthcheck:      http://localhost:8000/health"
    echo "    Docs (dev):       http://localhost:8000/docs"
    echo "  - MariaDB:          localhost:3306 (nativo)"
    echo "  - Redis:            localhost:6379 (nativo)"
    echo "  - Ollama:           http://localhost:11434 (nativo)"
    echo "  - cloudflared:      túnel nativo"
    echo ""
    echo "Instancia development:"
    echo "  - Instance ID: development"
    echo "  - Company: Development Empresa SL"
    echo "  - Webhook path: /webhook/development"
    echo ""
    echo "Comandos útiles:"
    echo "  make dev-status          # Ver estado de servicios"
    echo "  make dev-logs            # Ver logs de Hermes (journalctl)"
    echo "  make dev-health          # Healthcheck completo"
    echo "  make dev-stop            # Parar desarrollo"
    echo "  make dev-restart         # Reiniciar desarrollo"
    echo ""
    echo "Para Telegram: usa el bot token configurado en instances/development/instance.env"
    echo "(el config.yml usa secrets_refs y no contiene secretos reales)"
    echo ""
}

main() {
    echo ""
    echo "=== INICIANDO DEVELOPMENT GESTOR-IA (NATIVO) ==="
    echo ""
    
    check_sudo
    start_services
    check_health
    show_status
}

main "$@"