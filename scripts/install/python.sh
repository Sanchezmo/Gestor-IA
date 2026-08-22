#!/usr/bin/env bash
# scripts/install/python.sh
# Crear virtualenv Python e instalar dependencias

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

VENV_DIR="${PROJECT_ROOT}/.venv"

if [[ -d "$VENV_DIR" ]]; then
    log_warn "Virtualenv ya existe en $VENV_DIR"
else
    log_info "Creando virtualenv en $VENV_DIR..."
    python3 -m venv "$VENV_DIR"
fi

# Actualizar pip
"$VENV_DIR/bin/pip" install --upgrade pip setuptools wheel

# Instalar dependencias del proyecto
log_info "Instalando dependencias Python..."
"$VENV_DIR/bin/pip" install -e "$PROJECT_ROOT[dev]"

# Instalar dependencias opcionales
"$VENV_DIR/bin/pip" install -e "$PROJECT_ROOT[celery,postgresql,ai]"

log_info "Virtualenv Python creado y dependencias instaladas"
log_info "Activar con: source $VENV_DIR/bin/activate"