#!/usr/bin/env bash
# scripts/demo/start-demo.sh
# Iniciar entorno de demo completo: Docker Compose + Hermes

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

check_docker() {
    if ! command -v docker &> /dev/null; then
        log_error "Docker no está instalado"
        exit 1
    fi
    if ! docker compose version &> /dev/null; then
        log_error "Docker Compose no está disponible"
        exit 1
    fi
}

start_infrastructure() {
    log_step "Iniciando infraestructura Docker (MariaDB, Redis, Dolibarr, Ollama)..."
    cd "$PROJECT_ROOT"
    
    # Crear red si no existe
    docker network create demo-network 2>/dev/null || true
    
    # Levantar servicios
    docker compose -f docker-compose.demo.yml up -d
    
    log_info "Esperando a que los servicios estén listos..."
    
    # Esperar MariaDB
    log_info "Esperando MariaDB..."
    for i in {1..30}; do
        if docker exec mariadb-demo mysqladmin ping -h localhost -u root -p***REMOVED*** >/dev/null 2>&1; then
            log_info "MariaDB listo"
            break
        fi
        sleep 2
    done
    
    # Esperar Redis
    log_info "Esperando Redis..."
    for i in {1..15}; do
        if docker exec redis-demo redis-cli -a ***REMOVED*** ping >/dev/null 2>&1; then
            log_info "Redis listo"
            break
        fi
        sleep 1
    done
    
    # Esperar Dolibarr
    log_info "Esperando Dolibarr (puede tardar 60-120s en primera ejecución)..."
    for i in {1..60}; do
        if curl -sf http://localhost:8081/index.php >/dev/null 2>&1; then
            log_info "Dolibarr listo"
            break
        fi
        sleep 3
    done
    
    # Esperar Ollama
    log_info "Esperando Ollama..."
    for i in {1..30}; do
        if curl -sf http://localhost:11434/api/tags >/dev/null 2>&1; then
            log_info "Ollama listo"
            break
        fi
        sleep 2
    done
}

start_hermes() {
    log_step "Iniciando Hermes Core..."
    cd "$PROJECT_ROOT"
    
    # Activar virtualenv
    if [[ -f ".venv/bin/activate" ]]; then
        source .venv/bin/activate
    else
        log_error "Virtualenv no encontrado. Ejecuta: make install-python"
        exit 1
    fi
    
    # Configurar variables de entorno para demo
    export GESTOR_IA_ADMIN_TOKEN="demo_admin_token_123"
    export MARIADB_ROOT_PASSWORD="***REMOVED***"
    export REDIS_PASSWORD="***REMOVED***"
    export REDIS_HOST="127.0.0.1"
    export REDIS_PORT="6379"
    
    # Iniciar Hermes en background
    log_info "Iniciando Hermes API en puerto 8000..."
    nohup python -m uvicorn core.hermes.main:app --host 0.0.0.0 --port 8000 --reload > /tmp/hermes-demo.log 2>&1 &
    HERMES_PID=$!
    echo $HERMES_PID > /tmp/hermes-demo.pid
    
    # Esperar a que Hermes esté listo
    log_info "Esperando Hermes API..."
    for i in {1..15}; do
        if curl -sf http://localhost:8000/health >/dev/null 2>&1; then
            log_info "Hermes API listo"
            break
        fi
        sleep 1
    done
}

run_healthcheck() {
    log_step "Ejecutando healthcheck completo..."
    cd "$PROJECT_ROOT"
    if [[ -f ".venv/bin/activate" ]]; then
        source .venv/bin/activate
    fi
    python -m core.hermes.cli healthcheck
}

show_status() {
    echo ""
    echo "=========================================="
    echo "  DEMO GESTOR-IA LISTO"
    echo "=========================================="
    echo ""
    echo "Servicios:"
    echo "  - Dolibarr ERP:     http://localhost:8081"
    echo "    Usuario: admin / admin123"
    echo "  - Hermes API:       http://localhost:8000"
    echo "    Healthcheck:      http://localhost:8000/health"
    echo "    Docs (dev):       http://localhost:8000/docs"
    echo "  - MariaDB:          localhost:3306"
    echo "    Root: root / ***REMOVED***"
    echo "    DB: dolibarr_demo / ***REMOVED***"
    echo "  - Redis:            localhost:6379 (password: ***REMOVED***)"
    echo "  - Ollama:           http://localhost:11434"
    echo ""
    echo "Instancia demo:"
    echo "  - Instance ID: demo_empresa"
    echo "  - Company: Demo Empresa SL"
    echo "  - Webhook path: /webhook/demo_empresa"
    echo ""
    echo "Comandos útiles:"
    echo "  make status          # Ver estado de servicios"
    echo "  make dev-logs        # Ver logs de Hermes"
    echo "  ./scripts/demo/stop-demo.sh  # Parar demo"
    echo ""
    echo "Para Telegram: configura un bot y usa el token en instances/demo_empresa/config.yml"
    echo ""
}

main() {
    echo ""
    echo "=== INICIANDO DEMO GESTOR-IA ==="
    echo ""
    
    check_docker
    start_infrastructure
    start_hermes
    run_healthcheck
    show_status
}

main "$@"