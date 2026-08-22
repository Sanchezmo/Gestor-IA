#!/usr/bin/env bash
# scripts/install.sh
# Orquestador principal de instalación nativa Gestor-IA
# Uso: ./scripts/install.sh [--skip-deps] [--skip-db] [--skip-hermes]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# Cargar .env si existe
if [[ -f "${PROJECT_ROOT}/.env" ]]; then
    set -a
    source "${PROJECT_ROOT}/.env"
    set +a
fi

# Flags
SKIP_DEPS=false
SKIP_DB=false
SKIP_HERMES=false

while [[ $# -gt 0 ]]; do
    case $1 in
        --skip-deps) SKIP_DEPS=true; shift ;;
        --skip-db) SKIP_DB=true; shift ;;
        --skip-hermes) SKIP_HERMES=true; shift ;;
        --help|-h)
            echo "Uso: $0 [--skip-deps] [--skip-db] [--skip-hermes]"
            echo "  --skip-deps   : Omitir instalación de dependencias base"
            echo "  --skip-db     : Omitir MariaDB/Redis"
            echo "  --skip-hermes : Omitir preparación de Hermes"
            exit 0
            ;;
        *) log_error "Opción desconocida: $1"; exit 1 ;;
    esac
done

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

verify_env() {
    log_step "Verificando .env..."
    
    if [[ ! -f "${PROJECT_ROOT}/.env" ]]; then
        log_error "No se encuentra .env en ${PROJECT_ROOT}"
        log_info "Copia .env.example a .env y configura:"
        echo "  cp ${PROJECT_ROOT}/.env.example ${PROJECT_ROOT}/.env"
        echo "  nano ${PROJECT_ROOT}/.env"
        exit 1
    fi
    
    # Verificar variables críticas
    local critical_vars=(
        "MARIADB_ROOT_PASSWORD"
        "REDIS_PASSWORD"
        "JWT_SECRET_KEY"
        "FERNET_KEY"
    )
    
    local missing=()
    for var in "${critical_vars[@]}"; do
        if [[ -z "${!var:-}" ]]; then
            missing+=("$var")
        fi
    done
    
    if [[ ${#missing[@]} -gt 0 ]]; then
        log_warn "Variables críticas vacías en .env:"
        for var in "${missing[@]}"; do
            log_warn "  - $var"
        done
        log_info "Generar passwords: openssl rand -base64 32"
        log_info "Generar Fernet: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
        read -p "¿Continuar de todas formas? (y/N): " -n 1 -r
        echo
        [[ $REPLY =~ ^[Yy]$ ]] || exit 1
    fi
    
    log_info ".env verificado"
}

run_script() {
    local script="$1"
    local description="$2"
    
    log_step "$description"
    
    if [[ -f "${SCRIPT_DIR}/install/${script}" ]]; then
        bash "${SCRIPT_DIR}/install/${script}"
    else
        log_error "Script no encontrado: ${SCRIPT_DIR}/install/${script}"
        exit 1
    fi
}

main() {
    echo ""
    echo "=========================================="
    echo "  GESTOR-IA - INSTALACIÓN NATIVA"
    echo "=========================================="
    echo ""
    log_info "Proyecto: ${PROJECT_ROOT}"
    log_info "Entorno: ${GESTOR_IA_ENV:-development}"
    echo ""
    
    check_root
    verify_env
    
    # 1. Dependencias base
    if [[ "$SKIP_DEPS" != true ]]; then
        run_script "dependencies.sh" "Instalando dependencias base del sistema"
    else
        log_warn "Saltando dependencias base (--skip-deps)"
    fi
    
    # 2. Bases de datos
    if [[ "$SKIP_DB" != true ]]; then
        run_script "mariadb.sh" "Instalando MariaDB"
        run_script "redis.sh" "Instalando Redis"
    else
        log_warn "Saltando bases de datos (--skip-db)"
    fi
    
    # 3. PHP + Apache (para Dolibarr)
    run_script "php.sh" "Instalando PHP y extensiones para Dolibarr"
    run_script "apache.sh" "Instalando y configurando Apache2"
    
    # 4. Ollama
    run_script "ollama.sh" "Instalando Ollama nativo y modelo por defecto"
    
    # 5. Cloudflare
    run_script "cloudflare.sh" "Instalando cloudflared nativo"
    
    # 6. Python + Hermes
    if [[ "$SKIP_HERMES" != true ]]; then
        run_script "python.sh" "Creando virtualenv Python e instalando dependencias"
        run_script "hermes.sh" "Preparando Hermes Core (systemd)"
    else
        log_warn "Saltando Hermes (--skip-hermes)"
    fi
    
    echo ""
    echo "=========================================="
    echo "  INSTALACIÓN BASE COMPLETADA"
    echo "=========================================="
    echo ""
    log_info "Próximos pasos:"
    echo "  1. Configurar servicios:"
    echo "     sudo ${SCRIPT_DIR}/configure.sh"
    echo ""
    echo "  2. Iniciar servicios:"
    echo "     sudo ${SCRIPT_DIR}/services/start.sh"
    echo ""
    echo "  3. Verificar estado:"
    echo "     make status"
    echo ""
    echo "  4. Configurar Cloudflare Tunnel (requiere login manual):"
    echo "     cloudflared tunnel login"
    echo "     ${SCRIPT_DIR}/configure/cloudflare.sh"
    echo ""
}

main "$@"